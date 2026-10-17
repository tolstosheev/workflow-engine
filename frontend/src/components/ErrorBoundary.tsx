/** Без границы необработанное исключение оставляет белый экран без слов. */


import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Компонент-стенка не знает, как логировать; консоль — единственное
    // место, где ошибка доступна после того, как экран заменён заглушкой.
    console.error('ui.error', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <section
        role="alert"
        className="rounded-lg border border-danger/40 bg-danger/10 p-6"
      >
        <h1 className="text-lg font-semibold text-danger">Интерфейс не смог отобразить страницу</h1>
        <p className="mt-2 text-sm text-white/70">
          Данные при этом на месте: процессы продолжают исполняться, а журнал событий доступен на
          странице процессов.
        </p>
        <pre className="mt-3 overflow-auto rounded bg-surface p-3 text-xs text-white/60">
          {error.message}
        </pre>
        <button
          type="button"
          className="mt-4 rounded-md border border-surface-border px-3 py-1.5 text-sm text-white/80"
          onClick={() => this.setState({ error: null })}
        >
          Попробовать снова
        </button>
      </section>
    )
  }
}