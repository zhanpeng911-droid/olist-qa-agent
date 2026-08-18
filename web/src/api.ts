import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 300000 })  // 全量归因可达 2-3 分钟

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

// ---------- 会话历史（MySQL 持久化） ----------
export async function listSessions(): Promise<any[]> {
  const { data } = await http.get('/sessions')
  return data.sessions ?? []
}

export async function createSession(title = '新对话'): Promise<any> {
  const { data } = await http.post('/sessions', { title })
  return data.session
}

export async function renameSession(id: string, title: string) {
  await http.post(`/sessions/${id}/rename`, { title })
}

export async function deleteSession(id: string) {
  await http.delete(`/sessions/${id}`)
}

export async function getMessages(sid: string): Promise<any[]> {
  const { data } = await http.get(`/sessions/${sid}/messages`)
  return data.messages ?? []
}

export async function saveMessages(sid: string, messages: any[]) {
  await http.post(`/sessions/${sid}/messages`, { messages })
}

/** SSE 流式对话：onEvent(event, data)，结束时 onDone()（finally 保证必然回调） */
export async function chatStream(
  question: string,
  onEvent: (event: string, data: any) => void,
  onDone: () => void,
  onError: (err: any) => void,
) {
  let finished = false
  const finish = () => { if (!finished) { finished = true; onDone() } }
  const fail = (e: any) => { if (!finished) { finished = true; onError(e) } }
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
    finish()
  } catch (e) {
    fail(e)
  }
}
