/** Типы контракта API. Соответствуют схемам Pydantic в `engine/app/schemas/api.py`. */

export type InstanceStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'WAITING'
  | 'CANCELLING'
  | 'COMPENSATING'
  | 'COMPENSATION_FAILED'
  | 'COMPLETED'
  | 'COMPENSATED'
  | 'CANCELLED'
  | 'FAILED'

export type StepRunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'COMPENSATED'

export type EventType =
  | 'INSTANCE_CREATED'
  | 'INSTANCE_STARTED'
  | 'STEP_STARTED'
  | 'STEP_COMPLETED'
  | 'STEP_FAILED'
  | 'STEP_RETRY_SCHEDULED'
  | 'TIMER_SCHEDULED'
  | 'TIMER_FIRED'
  | 'COMPENSATION_STARTED'
  | 'COMPENSATION_COMPLETED'
  | 'COMPENSATION_FAILED'
  | 'COMPENSATION_SKIPPED'
  | 'INSTANCE_COMPLETED'
  | 'INSTANCE_COMPENSATED'
  | 'INSTANCE_CANCEL_REQUESTED'
  | 'INSTANCE_CANCELLED'
  | 'LEASE_EXPIRED'

export interface DefinitionRef {
  id: string
  name: string
  version: number
}

export interface DefinitionSummary {
  id: string
  name: string
  version: number
  is_active: boolean
  steps: string[]
  created_at: string
}

export interface WorkflowDefinition {
  id: string
  name: string
  version: number
  is_active: boolean
  definition: WorkflowDefinitionSchema
  created_at: string
  updated_at: string
}

export interface RetryPolicy {
  max_attempts: number
  backoff_seconds: number
  backoff_multiplier: number
}

export interface StepDefinition {
  action: string
  compensation?: string | null
  next?: string | Record<string, string> | null
  retry?: RetryPolicy | null
  wait_before_seconds?: number
  timeout_seconds?: number
  loop?: boolean
  max_iterations?: number | null
  description?: string | null
  fork?: Record<string, string[]>
  join?: string[]
}

export interface WorkflowDefinitionSchema {
  start: string
  steps: Record<string, StepDefinition>
  description?: string | null
  metadata?: Record<string, string>
}

export interface WorkflowInstance {
  id: string
  definition: DefinitionRef
  status: InstanceStatus
  current_step: string | null
  input: Record<string, unknown>
  variables: Record<string, unknown>
  error: string | null
  lease_owner: string | null
  lease_expires_at: string | null
  heartbeat_at: string | null
  created_at: string
  updated_at: string
}

export interface WorkflowEvent {
  seq: number
  type: EventType
  payload: Record<string, unknown>
  created_at: string
}

export interface StepRun {
  id: string
  step_name: string
  attempt: number
  status: StepRunStatus
  output: Record<string, unknown> | null
  error: string | null
  compensated: boolean
  started_at: string
  finished_at: string | null
}

export interface TimelineEntry {
  kind: 'step' | 'event'
  step_name?: string | null
  attempt?: number | null
  status?: string | null
  error?: string | null
  compensated?: boolean | null
  seq?: number | null
  type?: string | null
  payload?: Record<string, unknown> | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface InstanceState {
  status: InstanceStatus
  current_step: string | null
  variables: Record<string, unknown>
  completed_steps: string[]
  pending_compensations: string[]
  is_terminal: boolean
  /** Сколько раз шаг успешно завершился: для показа «проход 2 из 3». */
  step_iterations: Record<string, number>
}

export interface ValidationIssue {
  code: string
  message: string
  step: string | null
}

export interface ValidationResult {
  valid: boolean
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
}

export interface Paginated<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface ChaosResult {
  active: string[]
  message: string
}

export interface ActionResult {
  status: InstanceStatus
  message: string
}

export interface StreamEvent {
  instance_id: string
  type: EventType
  payload: Record<string, unknown>
  created_at: string
}