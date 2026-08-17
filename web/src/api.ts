import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

export async function getIntent(question: string): Promise<string> {
  const { data } = await http.post('/intent', { question })
  return data.intent
}

export async function runQuery(question: string) {
  return (await http.post('/query', { question })).data
}

export async function runStatistical(question: string) {
  return (await http.post('/statistical', { question })).data
}

export async function runAttribution(question: string) {
  return (await http.post('/attribution', { question })).data
}

export async function getMeta() {
  return (await http.get('/meta')).data
}

/** SSE 流式对话：onEvent(event, data)，结束时 onDone() */
export async function chatStream(
  question: string,
  onEvent: (event: string, data: any) => void,
  onDone: () => void,
  onError: (err: any) => void,
) {
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    })
    if (!resp.body) throw new Error('浏览器不支持流式读取')
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        let event = 'message'
        let data = ''
        for (const line of chunk.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) {
          try { onEvent(event, JSON.parse(data)) } catch { onEvent(event, data) }
        }
      }
    }
    onDone()
  } catch (e) {
    onError(e)
  }
}
