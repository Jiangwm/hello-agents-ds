from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CHAPTER_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = CHAPTER_DIR / "data" / "maintenance_logs.jsonl"


@dataclass(frozen=True)
class Scenario:
    equipment_type: str
    category: str
    symptom: str
    cause: str
    action: str
    fault_code: str
    downtime: int
    keywords: tuple[str, ...]


SCENARIOS = (
    Scenario(
        "CNC加工中心",
        "机械",
        "CNC-01主轴温度升高并伴随轻微异响",
        "主轴风道积尘导致散热不良",
        "清理主轴风道并复测温升",
        "MEC-SPINDLE-HEAT",
        28,
        ("CNC-01", "主轴", "温度升高", "风道"),
    ),
    Scenario(
        "输送机",
        "机械",
        "输送机驱动端轴承温度高且振动达到7.2 mm/s",
        "轴承润滑脂劣化",
        "更换轴承并补充指定润滑脂",
        "MEC-BEARING-001",
        45,
        ("轴承", "温度高", "7.2 mm/s", "润滑"),
    ),
    Scenario(
        "包装机",
        "机械",
        "包装机同步带打滑并出现节拍抖动",
        "同步带张紧力不足",
        "调整张紧轮并校正同步带",
        "MEC-BELT-002",
        22,
        ("同步带", "打滑", "张紧"),
    ),
    Scenario(
        "减速机",
        "机械",
        "减速机运行时周期性异响且齿侧间隙偏大",
        "齿轮磨损造成啮合间隙增大",
        "更换磨损齿轮并校核啮合间隙",
        "MEC-GEAR-003",
        70,
        ("减速机", "异响", "齿轮", "间隙"),
    ),
    Scenario(
        "伺服系统",
        "电气",
        "L-01伺服出现X轴跟随误差报警",
        "编码器零位漂移",
        "重新标定编码器零位",
        "ELE-SERVO-011",
        18,
        ("伺服", "X轴", "跟随误差", "编码器"),
    ),
    Scenario(
        "PLC控制柜",
        "电气",
        "PLC-01的I/O信号间歇丢失",
        "端子排接插件松动",
        "紧固端子排并复测I/O信号",
        "ELE-PLC-012",
        16,
        ("PLC-01", "I/O", "信号丢失", "端子"),
    ),
    Scenario(
        "三相电机",
        "电气",
        "M-02电机绕组温度高，启动后电流偏高并触发过载",
        "接线端子接触电阻增大",
        "重做电机端子接线并测量三相电流",
        "ELE-MOTOR-013",
        32,
        ("M-02", "电机", "温度高", "电流偏高", "过载", "端子"),
    ),
    Scenario(
        "变频器",
        "电气",
        "VFD-03减速阶段频繁报母线过压",
        "制动电阻回路开路",
        "修复制动电阻回路并验证减速曲线",
        "ELE-VFD-014",
        25,
        ("VFD-03", "母线过压", "制动电阻"),
    ),
    Scenario(
        "液压缸",
        "液压",
        "HYC-01液压缸低速动作爬行",
        "液压回路混入空气",
        "执行排气并补充过滤后的液压油",
        "HYD-CYL-021",
        24,
        ("HYC-01", "液压缸", "爬行", "排气"),
    ),
    Scenario(
        "液压站",
        "液压",
        "HYD-02液压站压力偏低且泵噪声增大",
        "吸油滤芯堵塞",
        "更换吸油滤芯并检查液位",
        "HYD-PUMP-022",
        38,
        ("HYD-02", "压力偏低", "滤芯", "噪声"),
    ),
    Scenario(
        "比例阀",
        "液压",
        "PV-03比例阀响应迟滞并偶发卡滞",
        "阀芯受到颗粒污染",
        "清洗阀芯并更换回油过滤器",
        "HYD-VALVE-023",
        42,
        ("PV-03", "比例阀", "迟滞", "卡滞"),
    ),
    Scenario(
        "液压管路",
        "液压",
        "H-04高压软管接头持续渗油",
        "接头密封圈老化",
        "泄压后更换密封圈并保压复检",
        "HYD-LEAK-024",
        30,
        ("H-04", "渗油", "密封圈", "保压"),
    ),
    Scenario(
        "CNC加工中心",
        "工艺",
        "CNC-02加工表面粗糙度超差",
        "刀具磨损且进给速度偏高",
        "更换刀具并恢复已审核的进给参数",
        "PRO-ROUGHNESS-031",
        20,
        ("CNC-02", "粗糙度", "刀具磨损", "进给"),
    ),
    Scenario(
        "磨床",
        "工艺",
        "GRD-01工件尺寸连续偏大0.04 mm",
        "砂轮磨损补偿未及时更新",
        "修整砂轮并由工艺员复核补偿值",
        "PRO-SIZE-032",
        26,
        ("GRD-01", "尺寸偏大", "0.04 mm", "补偿"),
    ),
    Scenario(
        "焊接工作站",
        "工艺",
        "WELD-02焊缝出现密集气孔",
        "保护气体流量不足",
        "检查气路并恢复工艺卡规定流量",
        "PRO-WELD-033",
        34,
        ("WELD-02", "气孔", "保护气体", "流量"),
    ),
    Scenario(
        "热处理炉",
        "工艺",
        "FUR-03保温段温度波动超过工艺窗口",
        "热电偶漂移造成PID输入偏差",
        "更换热电偶并由工艺员确认PID参数",
        "PRO-TEMP-034",
        48,
        ("FUR-03", "温度波动", "热电偶", "PID"),
    ),
    Scenario(
        "装配线",
        "未知",
        "ASM-01偶发停机但现场未保留报警码",
        "现有记录不足以定位原因",
        "保留现场日志并转设备工程师复核",
        "UNK-041",
        12,
        ("ASM-01", "偶发停机", "报警码缺失"),
    ),
    Scenario(
        "空压机",
        "未知",
        "ACP-02出现短时异响后自行恢复",
        "异响来源尚未确认",
        "增加巡检频次并采集振动频谱",
        "UNK-042",
        8,
        ("ACP-02", "短时异响", "待确认"),
    ),
    Scenario(
        "机器人",
        "未知",
        "ROB-03间歇报警且复位后未复现",
        "缺少完整报警上下文",
        "导出只读诊断日志并安排人工复核",
        "UNK-043",
        10,
        ("ROB-03", "间歇报警", "未复现"),
    ),
    Scenario(
        "灌装机",
        "未知",
        "FIL-04操作员报告节拍不稳但测量值正常",
        "事实证据不足",
        "记录批次和时间窗口并继续观察",
        "UNK-044",
        6,
        ("FIL-04", "节拍不稳", "证据不足"),
    ),
    Scenario(
        "冷却风机",
        "机械",
        "FAN-05叶轮振动升高并伴随周期性异响",
        "叶轮积灰造成动平衡偏差",
        "清理叶轮并复核动平衡",
        "MEC-FAN-005",
        36,
        ("FAN-05", "叶轮", "振动升高", "动平衡"),
    ),
    Scenario(
        "接触器",
        "电气",
        "KM-06吸合后线圈电流偏高且触点抖动",
        "线圈绝缘劣化",
        "更换接触器并复测控制回路电流",
        "ELE-CONTACTOR-015",
        19,
        ("KM-06", "线圈", "电流偏高", "触点抖动"),
    ),
    Scenario(
        "蓄能器",
        "液压",
        "ACC-07保压阶段压力持续下降",
        "蓄能器预充压力不足",
        "泄压后按规程复核预充压力",
        "HYD-ACC-025",
        27,
        ("ACC-07", "保压", "压力下降", "预充压力"),
    ),
    Scenario(
        "涂布机",
        "工艺",
        "COAT-08涂层厚度连续低于工艺下限",
        "供料流量偏低",
        "由工艺员复核供料流量与速度设定",
        "PRO-COAT-035",
        31,
        ("COAT-08", "涂层厚度", "工艺下限", "流量"),
    ),
    Scenario(
        "视觉检测站",
        "未知",
        "VIS-09出现单次测量跳变且复测未复现",
        "缺少原始图像与同步测量证据",
        "保存原始数据并转质量工程师复核",
        "UNK-045",
        7,
        ("VIS-09", "测量跳变", "未复现", "证据不足"),
    ),
)


def _raw_texts(scenario: Scenario) -> tuple[str, ...]:
    return (
        f"{scenario.equipment_type}{scenario.symptom}，{scenario.action}后恢复。",
        (
            f"{scenario.equipment_type}巡检发现{scenario.symptom}，"
            f"检查记录为{scenario.cause}，已{scenario.action}。"
        ),
        (
            f"{scenario.equipment_type}报警：{scenario.symptom}；"
            f"处理措施为{scenario.action}，设备恢复。"
        ),
        (
            f"{scenario.equipment_type}{scenario.symptom}，"
            f"原因记录为{scenario.cause}，{scenario.action}后试运行正常。"
        ),
    )


def build_rows() -> tuple[dict[str, object], ...]:
    rows = []
    log_number = 1
    for scenario_number, scenario in enumerate(SCENARIOS, start=1):
        for variant, raw_text in enumerate(
            _raw_texts(scenario),
            start=1,
        ):
            month = ((scenario_number - 1) % 4) + 1
            day = ((scenario_number - 1) // 4) + 1
            if scenario_number > 20:
                month = 5
                day = scenario_number - 20
            event_time = datetime(
                2026,
                month,
                day,
                8 + scenario_number % 10,
                variant * 5,
            )
            log_id = f"ML-{log_number:03d}"
            source_event_id = f"EVT-{scenario_number:03d}"
            rows.append(
                {
                    "log_id": log_id,
                    "equipment_type": scenario.equipment_type,
                    "event_time": event_time.isoformat(timespec="minutes"),
                    "raw_text": raw_text,
                    "fault_category": scenario.category,
                    "action_taken": scenario.action,
                    "downtime_minutes": scenario.downtime,
                    "keywords": list(scenario.keywords),
                    "fault_code": scenario.fault_code,
                    "source_event_id": source_event_id,
                }
            )
            log_number += 1
    return tuple(rows)


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in build_rows()
    )
    OUTPUT_PATH.write_text(content + "\n", encoding="utf-8")
    print(f"已生成 {len(build_rows())} 条脱敏教学日志：{OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
