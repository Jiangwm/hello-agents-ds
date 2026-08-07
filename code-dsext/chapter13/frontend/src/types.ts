export type OptimizationObjective = 'balanced' | 'cost' | 'peak'
export type ApprovalStatus = 'pending_approval' | 'approved'

export interface OperationConstraint {
  constraint_id: string
  task_id: string
  kind: 'fixed' | 'window' | 'precedence' | 'duration' | 'resource' | 'peak_limit'
  value: string | number | number[]
  related_task_id?: string | null
}

export interface PlanningRequest {
  line_id: string
  planning_date: string
  output_target_units: number
  shifts: Array<'day' | 'night'>
  optimization_objective: OptimizationObjective
  tariff_profile_id: string
  locked_task_ids: string[]
  constraints: OperationConstraint[]
}

export interface ProductionTask {
  task_id: string
  product: string
  batch_size: number
  device_candidates: string[]
  selected_device_id: string
  due_minute: number
  duration_minutes: number
  power_kw: number
  start_minute: number
  movable: boolean
  earliest_start_minute: number
  latest_end_minute: number
  locked: boolean
}

export interface EnergyPoint {
  start_minute: number
  power_kw: number
  energy_kwh: number
  tariff_period: string
  price_per_kwh: number
}

export interface EnergyCurve {
  interval_minutes: 30
  points: EnergyPoint[]
}

export interface CostBreakdown {
  by_period: Record<string, number>
  total_cost: number
}

export interface ConstraintViolation {
  constraint_id: string
  task_id: string | null
  message: string
  severity: 'error' | 'warning'
}

export interface RecommendationEvidence {
  tool_call_ids: string[]
  content_hashes: string[]
  source_window: string
  data_version: string
  model_version: string
  assumptions: string[]
  estimation_error: string
  reviewed: boolean
  read_only: true
  production_control: 'prohibited'
}

export interface ScheduleOption {
  option_id: string
  name: string
  strategy: 'baseline' | 'cost' | 'peak' | 'balanced'
  tasks: ProductionTask[]
  curve: EnergyCurve
  cost: CostBreakdown
  total_energy_kwh: number
  peak_kw: number
  unit_energy_kwh: number
  confidence_interval_kwh: [number, number]
  violations: ConstraintViolation[]
  feasible: boolean
  evidence: RecommendationEvidence | null
}

export interface ChangeRecord {
  change_id: string
  content_hash: string
  changed_at: string
  option_id: string
  task_id: string
  before: Record<string, number | boolean>
  after: Record<string, number | boolean>
  actor: string
  reason: string
}

export interface ApprovalRecord {
  approval_id: string
  input_hash: string
  approver: string
  reason: string
  approved_at: string
}

export interface DataSummary {
  source_window: string
  data_version: string
  model_version: string
  status: 'ready' | 'degraded'
  issues: string[]
  task_count: number
  baseline_count: number
}

export interface EnergyPlan {
  plan_id: string
  request: PlanningRequest
  effective_constraints: OperationConstraint[]
  baseline: ScheduleOption
  candidates: ScheduleOption[]
  recommended_option_id: string
  approval_status: ApprovalStatus
  approval: ApprovalRecord | null
  progress: string[]
  recomputed_task_ids: string[]
  change_history: ChangeRecord[]
  data_summary: DataSummary
  assumptions: string[]
  estimation_error_percent: number
  evidence: RecommendationEvidence[]
  read_only: true
  production_control: 'prohibited'
}

export interface TaskScheduleEdit {
  task_id: string
  start_minute?: number
  locked?: boolean
}

export interface PlanChangeRequest {
  option_id: string
  actor: string
  reason: string
  changes: TaskScheduleEdit[]
}

export interface ApprovalRequest {
  approver: string
  reason: string
}
