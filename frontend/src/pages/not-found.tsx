import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <section className="text-center">
      <h1 className="text-xl font-semibold">Страница не найдена</h1>
      <Link to="/" className="mt-4 inline-block text-sm text-accent hover:underline">
        Вернуться к списку заказов
      </Link>
    </section>
  )
}
