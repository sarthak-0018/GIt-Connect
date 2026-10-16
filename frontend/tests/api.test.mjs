import test from 'node:test'
import assert from 'node:assert/strict'
import { postAnalyze } from '../src/api.js'

function response(status, body, contentType = 'application/json') {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === 'content-type' ? contentType : null },
    text: async () => body,
  }
}

function fetchReturning(value) { return async () => value }

test('200 JSON response resolves to its payload', async () => {
  assert.deepEqual(await postAnalyze('https://github.com/a/b', fetchReturning(response(200, '{"ok":true}'))), { ok: true })
})

for (const [status, code] of [[400, 'INVALID_GITHUB_URL'], [404, 'REPOSITORY_NOT_FOUND'], [403, 'GITHUB_RATE_LIMIT'], [429, 'GITHUB_RATE_LIMIT'], [500, 'INTERNAL_SERVER_ERROR']]) {
  test(`${status} JSON error is converted to its human message`, async () => {
    const payload = { error: { code, message: `Readable ${code} message`, retryable: status >= 429, request_id: 'test-id' } }
    await assert.rejects(postAnalyze('https://github.com/a/b', fetchReturning(response(status, JSON.stringify(payload)))), new RegExp(`Readable ${code} message`))
  })
}

test('non-JSON server response uses a safe fallback and hides HTML/internal errors', async () => {
  await assert.rejects(postAnalyze('https://github.com/a/b', fetchReturning(response(500, '<html>Internal Server Error</html>', 'text/html'))), /Unable to analyze this repository/)
})

test('plain-text backend error can be displayed safely', async () => {
  await assert.rejects(postAnalyze('https://github.com/a/b', fetchReturning(response(503, 'GitHub API rate limit reached.', 'text/plain'))), /GitHub API rate limit reached/)
})

test('malformed JSON success never exposes a JSON parser error', async () => {
  await assert.rejects(postAnalyze('https://github.com/a/b', fetchReturning(response(200, 'Internal Server Error'))), /Unable to analyze this repository/)
})

test('malformed JSON error uses a safe fallback', async () => {
  await assert.rejects(postAnalyze('https://github.com/a/b', fetchReturning(response(500, '{broken', 'application/json'))), /Unable to analyze this repository/)
})

test('network failure returns a connection message', async () => {
  await assert.rejects(postAnalyze('https://github.com/a/b', async () => { throw new TypeError('Failed to fetch') }), /Could not reach ContribCompass/)
})
