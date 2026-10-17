import { describe, expect, it } from 'vitest'

const files = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('проба', () => {
  it('видит файлы', () => {
    expect(Object.keys(files).length).toBeGreaterThan(10)
    expect(files['/src/lib/status.ts']).toContain('STATUS_LABELS')
  })
})