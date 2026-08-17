/** 会话状态（模块级单例：侧边栏与对话页共享，localStorage 持久化） */
import { ref } from 'vue'

const STORAGE_KEY = 'olist_chat_sessions'

export interface ChatSession {
  id: string
  title: string
  messages: any[]
  updatedAt: number
}

const sessions = ref<ChatSession[]>([])
const currentId = ref('')
let loaded = false

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
}

function load() {
  if (loaded) return
  loaded = true
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const list = JSON.parse(raw)
      if (Array.isArray(list) && list.length) {
        sessions.value = list
        currentId.value = list[0].id
        return
      }
    }
  } catch { /* 损坏则新建 */ }
  const id = uid()
  sessions.value = [{ id, title: '新对话', messages: [], updatedAt: Date.now() }]
  currentId.value = id
}

function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.value)) } catch { /* 超限忽略 */ }
}

export function useSessions() {
  load()
  return {
    sessions,
    currentId,
    getMessages(): any[] {
      const s = sessions.value.find(x => x.id === currentId.value)
      return s?.messages ?? []
    },
    setMessages(msgs: any[]) {
      const s = sessions.value.find(x => x.id === currentId.value)
      if (s) s.messages = msgs
      persist()
    },
    newSession(): string {
      const id = uid()
      sessions.value.unshift({ id, title: '新对话', messages: [], updatedAt: Date.now() })
      currentId.value = id
      persist()
      return id
    },
    switchSession(id: string) {
      currentId.value = id
      persist()
    },
    deleteSession(id: string) {
      const i = sessions.value.findIndex(x => x.id === id)
      if (i < 0) return
      sessions.value.splice(i, 1)
      if (currentId.value === id) {
        currentId.value = sessions.value[Math.max(0, i - 1)]?.id ?? ''
      }
      persist()
    },
    setTitle(id: string, title: string) {
      const s = sessions.value.find(x => x.id === id)
      if (s && (!s.title || s.title === '新对话')) s.title = title
      persist()
    },
  }
}
