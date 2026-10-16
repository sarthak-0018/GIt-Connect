const FALLBACK_ERROR = 'Unable to analyze this repository.'

function isJsonContentType(value = '') {
  return /(?:application\/json|\+json)(?:\s*;|$)/i.test(value)
}

function safeTextError(text) {
  const message = text.trim()
  if (!message || message.startsWith('<') || /internal server error|unexpected token|not valid json/i.test(message)) return FALLBACK_ERROR
  return message.slice(0, 300)
}

function errorMessage(payload, responseText = '') {
  const message = payload?.error?.message ?? payload?.detail?.message ?? payload?.detail
  if (typeof message === 'string' && message.trim()) {
    const retryAt = payload?.error?.retry_at
    return `${message}${retryAt ? ` Retry after ${new Date(retryAt).toLocaleString()}.` : ''}`
  }
  return safeTextError(responseText)
}

/** Fetch and validate the ContribCompass JSON API without leaking parser errors to users. */
export async function postAnalyze(url, fetcher = fetch) {
  let response
  try {
    response = await fetcher('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ url }),
    })
  } catch {
    throw new Error('Could not reach ContribCompass. Check your connection and try again.')
  }

  const successful = response.ok
  const contentType = response.headers?.get?.('content-type') ?? ''
  const expectsJson = isJsonContentType(contentType)
  let text = ''
  try {
    text = await response.text()
  } catch {
    // Some network and mocked responses cannot provide a body; status still determines fallback.
  }

  if (!successful) {
    let payload
    if (expectsJson && text) {
      try { payload = JSON.parse(text) } catch { /* use a safe status fallback below */ }
    }
    throw new Error(errorMessage(payload, expectsJson ? '' : text))
  }

  if (!expectsJson || !text) throw new Error(FALLBACK_ERROR)
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(FALLBACK_ERROR)
  }
}

export { FALLBACK_ERROR }
