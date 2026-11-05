import { describe, expect, it, vi } from 'vitest'

import { ApiError, api, toQueryString } from './client'

function respondWith(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify(body), { status }))
}

describe('toQueryString', () => {
  it('drops empty values and keeps false', () => {
    expect(toQueryString({ q: '', review: undefined, annotated: false })).toBe('?annotated=false')
  })

  it('is empty when nothing is left', () => {
    expect(toQueryString({ q: '' })).toBe('')
  })
})

describe('api', () => {
  it('starts an annotation run on a chosen backend', async () => {
    const fetchMock = respondWith(202, {})

    await api.annotate(20, 'claude')

    expect(fetchMock.mock.calls[0][1]?.body).toBe('{"limit":20,"backend":"claude"}')
  })

  it('asks for an AI image from words and photos', async () => {
    const fetchMock = respondWith(202, {})

    await api.imagine('a pier', [3])

    expect(fetchMock.mock.calls[0][1]?.body).toBe(
      '{"kind":"ai","prompt":"a pier","reference_ids":[3]}',
    )
  })

  it('asks for a relight of one photo', async () => {
    const fetchMock = respondWith(202, {})

    await api.relight(7)

    expect(fetchMock.mock.calls[0][1]?.body).toBe(
      '{"kind":"ai","preset":"relight","reference_ids":[7]}',
    )
  })

  it('asks the catalog for reference photos', async () => {
    const fetchMock = respondWith(200, { reference_ids: [1], why: 'x' })

    expect(await api.suggest('a pier')).toEqual({ reference_ids: [1], why: 'x' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ai/suggest')
  })

  it('sends an edit as a JSON PATCH', async () => {
    const fetchMock = respondWith(200, { id: 1 })

    await api.editPhoto(1, { review_status: 'approved' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/photos/1')
    expect(init?.method).toBe('PATCH')
    expect(init?.body).toBe('{"review_status":"approved"}')
  })

  it('names a composite by its kind', async () => {
    const fetchMock = respondWith(202, {})
    const settings = {
      center_x: 0.5,
      baseline_y: 0.9,
      height: 0.6,
      flip: false,
      color_match: 0.6,
      shadow_strength: 0.5,
      light_wrap: 0.3,
      light: 'left' as const,
    }

    await api.composite(1, 2, settings)

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body).toMatchObject({ kind: 'composite', subject_id: 1, background_id: 2, light: 'left' })
  })

  it("surfaces the server's own explanation", async () => {
    respondWith(409, { detail: 'ANTHROPIC_API_KEY is not set in .env' })

    await expect(api.stats()).rejects.toThrow(new ApiError('ANTHROPIC_API_KEY is not set in .env'))
  })
})
