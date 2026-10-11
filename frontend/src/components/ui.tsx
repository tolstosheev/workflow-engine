import type { ReactNode } from 'react'

interface StatusBadgeProps {
  label: string
  toneClass: string
}

/** Статусная метка: цвет и текст всегда читаются вместе, цвет ничего не значит без подписи. */
export function StatusBadge({ label, toneClass }: StatusBadgeProps) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs ${toneClass}`}>{label}</span>
  )
}

interface CardProps {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function Card({ title, actions, children, className = '' }: CardProps) {
  return (
    <section className={`rounded-lg border border-surface-border bg-surface-raised p-4 ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-3">
          {title}
          {actions}
        </header>
      )}
      {children}
    </section>
  )
}

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger'
}

const BUTTON_VARIANTS: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-accent/20 text-accent border-accent/40 hover:bg-accent/30',
  secondary: 'bg-surface-border text-white/80 hover:bg-surface-border/70',
  danger: 'bg-danger/20 text-danger border-danger/40 hover:bg-danger/30',
}

export function Button({ variant = 'secondary', className = '', ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={`rounded-md border px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_VARIANTS[variant]} ${className}`}
    />
  )
}

interface EmptyStateProps {
  message: string
}

export function EmptyState({ message }: EmptyStateProps) {
  return <p className="py-6 text-center text-sm text-white/50">{message}</p>
}

interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    // role=alert: без него скринридер не объявит об ошибке — текст в разметке
    // не прерывает чтение, и пользователь узнает о сбое только случайно.
    <div
      role="alert"
      className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger"
    >
      <p>{message}</p>
      {onRetry && (
        <Button className="mt-2" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  )
}