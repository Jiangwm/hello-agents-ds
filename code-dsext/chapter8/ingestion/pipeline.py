from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from governance import redact_sensitive_text
from schemas import DocumentStatus, KnowledgeChunk, SourceType


class IngestionError(ValueError):
    pass

class IngestionPipeline:
    _required_metadata = frozenset(
        {
            "document_id",
            "title",
            "version",
            "effective_date",
            "status",
            "source_type",
            "equipment_models",
            "allowed_roles",
            "license",
        }
    )

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()

    def ingest_directory(self, relative_directory: str = "documents") -> list[
        KnowledgeChunk
    ]:
        directory = self._safe_path(relative_directory)
        if not directory.is_dir():
            raise IngestionError(f"文档目录不存在：{relative_directory}")
        chunks: list[KnowledgeChunk] = []
        for path in sorted(directory.glob("*.md")):
            chunks.extend(self.ingest_markdown(path))
        if not chunks:
            raise IngestionError("文档目录中没有可摄取的 Markdown 文档")
        return chunks

    def ingest_csv(self, relative_path: str) -> list[KnowledgeChunk]:
        path = self._safe_path(relative_path)
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                rows = tuple(csv.DictReader(handle))
        except OSError as error:
            raise IngestionError(f"表格规程读取失败：{error}") from error
        chunks: list[KnowledgeChunk] = []
        for row_number, row in enumerate(rows, start=2):
            try:
                source_type = SourceType(row["source_type"].strip())
                status = DocumentStatus(row["status"].strip())
                effective_date = date.fromisoformat(
                    (
                        row.get("effective_date")
                        or row.get("event_date")
                        or ""
                    ).strip()
                )
                event_date = (
                    date.fromisoformat(row["event_date"].strip())
                    if source_type is SourceType.HISTORICAL
                    else None
                )
                document_id = row["document_id"].strip()
                paragraph_id = row["paragraph_id"].strip()
                identity = (
                    f"{document_id}|{row['version'].strip()}|{paragraph_id}"
                )
                chunks.append(
                    KnowledgeChunk(
                        chunk_id="CH-"
                        + hashlib.sha256(
                            identity.encode("utf-8")
                        ).hexdigest()[:12].upper(),
                        document_id=document_id,
                        title=row["title"].strip(),
                        title_path=(row["title"].strip(),),
                        paragraph_id=paragraph_id,
                        version=row["version"].strip(),
                        effective_date=effective_date,
                        status=status,
                        source_type=source_type,
                        equipment_models=self._csv_tuple(
                            row["equipment_model"]
                        ),
                        equipment_ids=self._csv_tuple(row["equipment_id"]),
                        allowed_roles=frozenset(
                            self._csv_tuple(row["allowed_roles"])
                        ),
                        license_name=row["license"].strip(),
                        content=redact_sensitive_text(
                            row["content"].strip()
                        ),
                        source_path=path.relative_to(
                            self.data_root
                        ).as_posix(),
                        event_date=event_date,
                    )
                )
            except (KeyError, ValueError) as error:
                raise IngestionError(
                    f"表格第 {row_number} 行不满足摄取契约：{error}"
                ) from error
        if not chunks:
            raise IngestionError("表格中没有可摄取的记录")
        return chunks

    def ingest_pdf(self, path: Path) -> list[KnowledgeChunk]:
        resolved = path.resolve()
        self._ensure_inside_root(resolved)
        sidecar = resolved.with_suffix(resolved.suffix + ".meta.json")
        self._ensure_inside_root(sidecar)
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise IngestionError(f"PDF 元数据读取失败：{error}") from error
        missing = sorted(self._required_metadata - metadata.keys())
        if missing:
            raise IngestionError(f"缺少 PDF 元数据：{', '.join(missing)}")
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise IngestionError(
                "PDF 摄取需要可选依赖 pypdf"
            ) from error
        try:
            effective_date = date.fromisoformat(
                str(metadata["effective_date"])
            )
            status = DocumentStatus(str(metadata["status"]))
            source_type = SourceType(str(metadata["source_type"]))
            reader = PdfReader(str(resolved))
        except (OSError, ValueError) as error:
            raise IngestionError(f"PDF 摄取失败：{error}") from error

        chunks: list[KnowledgeChunk] = []
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            page_text = (pdf_page.extract_text() or "").strip()
            paragraphs = tuple(
                paragraph.strip()
                for paragraph in re.split(r"\n\s*\n", page_text)
                if paragraph.strip()
            )
            for paragraph_number, content in enumerate(paragraphs, start=1):
                paragraph_id = (
                    f"page-{page_number:03d}-paragraph-"
                    f"{paragraph_number:03d}"
                )
                identity = "|".join(
                    (
                        str(metadata["document_id"]),
                        str(metadata["version"]),
                        paragraph_id,
                    )
                )
                chunks.append(
                    KnowledgeChunk(
                        chunk_id="CH-"
                        + hashlib.sha256(
                            identity.encode("utf-8")
                        ).hexdigest()[:12].upper(),
                        document_id=str(metadata["document_id"]),
                        title=str(metadata["title"]),
                        title_path=(
                            str(metadata["title"]),
                            f"第 {page_number} 页",
                        ),
                        paragraph_id=paragraph_id,
                        version=str(metadata["version"]),
                        effective_date=effective_date,
                        status=status,
                        source_type=source_type,
                        equipment_models=self._csv_tuple(
                            str(metadata["equipment_models"])
                        ),
                        equipment_ids=self._csv_tuple(
                            str(metadata.get("equipment_ids", ""))
                        ),
                        allowed_roles=frozenset(
                            self._csv_tuple(
                                str(metadata["allowed_roles"])
                            )
                        ),
                        license_name=str(metadata["license"]),
                        content=redact_sensitive_text(content),
                        source_path=resolved.relative_to(
                            self.data_root
                        ).as_posix(),
                        page=page_number,
                    )
                )
        if not chunks:
            raise IngestionError("PDF 中没有可摄取的文本")
        return chunks

    def ingest_markdown(self, path: Path) -> list[KnowledgeChunk]:
        resolved = path.resolve()
        self._ensure_inside_root(resolved)
        text = resolved.read_text(encoding="utf-8")
        metadata, body = self._parse_frontmatter(text)
        missing = sorted(self._required_metadata - metadata.keys())
        if missing:
            raise IngestionError(f"缺少文档元数据：{', '.join(missing)}")

        try:
            effective_date = date.fromisoformat(metadata["effective_date"])
            status = DocumentStatus(metadata["status"])
            source_type = SourceType(metadata["source_type"])
        except ValueError as error:
            raise IngestionError(f"文档元数据取值无效：{error}") from error

        common = {
            "document_id": metadata["document_id"],
            "title": metadata["title"],
            "version": metadata["version"],
            "effective_date": effective_date,
            "status": status,
            "source_type": source_type,
            "equipment_models": self._csv_tuple(metadata["equipment_models"]),
            "equipment_ids": self._csv_tuple(metadata.get("equipment_ids", "")),
            "allowed_roles": frozenset(
                self._csv_tuple(metadata["allowed_roles"])
            ),
            "license_name": metadata["license"],
            "source_path": resolved.relative_to(self.data_root).as_posix(),
        }
        return self._chunk_markdown(body, common)

    def _chunk_markdown(
        self,
        body: str,
        common: dict[str, object],
    ) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        title_path: list[str] = []
        paragraph_lines: list[str] = []
        page: int | None = None
        pending_paragraph_id: str | None = None

        def flush() -> None:
            nonlocal pending_paragraph_id
            content = "\n".join(paragraph_lines).strip()
            paragraph_lines.clear()
            if not content:
                return
            sequence = len(chunks) + 1
            paragraph_id = pending_paragraph_id or f"paragraph-{sequence:03d}"
            pending_paragraph_id = None
            identity = (
                f"{common['document_id']}|{common['version']}|{paragraph_id}"
            )
            chunk_id = "CH-" + hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()[:12].upper()
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    title_path=tuple(title_path),
                    paragraph_id=paragraph_id,
                    content=redact_sensitive_text(content),
                    page=page,
                    **common,
                )
            )

        for raw_line in body.splitlines():
            line = raw_line.rstrip()
            page_match = re.fullmatch(r"<!--\s*page:\s*(\d+)\s*-->", line)
            paragraph_match = re.fullmatch(
                r"<!--\s*paragraph-id:\s*([A-Za-z0-9_-]+)\s*-->",
                line,
            )
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if page_match:
                flush()
                page = int(page_match.group(1))
            elif paragraph_match:
                flush()
                pending_paragraph_id = paragraph_match.group(1)
            elif heading_match:
                flush()
                level = len(heading_match.group(1))
                title_path[level - 1 :] = [heading_match.group(2).strip()]
            elif not line.strip():
                flush()
            else:
                paragraph_lines.append(line)
        flush()
        if not chunks:
            raise IngestionError(f"文档 {common['document_id']} 没有正文片段")
        return chunks

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.data_root / relative_path).resolve()
        self._ensure_inside_root(candidate)
        return candidate

    def _ensure_inside_root(self, path: Path) -> None:
        try:
            path.relative_to(self.data_root)
        except ValueError as error:
            raise IngestionError("只允许读取 data_root 内的文档") from error

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise IngestionError("Markdown 必须包含简单 frontmatter")
        metadata: dict[str, str] = {}
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return metadata, "\n".join(lines[index + 1 :])
            if ":" not in line:
                raise IngestionError(f"frontmatter 行格式无效：{line}")
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        raise IngestionError("frontmatter 未闭合")

    @staticmethod
    def _csv_tuple(value: str) -> tuple[str, ...]:
        return tuple(
            item.strip()
            for item in re.split(r"[,|]", value)
            if item.strip()
        )
