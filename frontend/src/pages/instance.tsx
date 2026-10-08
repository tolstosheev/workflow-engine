import { useParams } from 'react-router-dom'

export default function InstancePage() {
  const { id } = useParams<{ id: string }>()

  return (
    <section>
      <h1 className="text-xl font-semibold">Инстанс {id}</h1>
      <p className="mt-2 text-sm text-white/60">
        Таймлайн шагов и журнал событий появятся в Фазе 4.
      </p>
    </section>
  )
}
