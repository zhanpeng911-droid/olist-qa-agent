/** 会话状态（模块级单例：侧边栏与对话页共享，MySQL 持久化） */
import { ref } from 'vue'
import * as api from '../api'

export interface ChatSession {
  id: string
  title: string
  messages: any[]
  messageCount?: number
  updatedAt: number
}

const sessions = ref<ChatSession[]>([])
const currentId = ref('')
let loaded = false

function localId() {
  return 'local-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
}

export function useSessions() {
  return {
    sessions,
    currentId,
    async loadSessions(): Promise<void> {
      loaded = true
      try {
        const list = await api.listSessions()
        sessions.value = list.map((s: any) => ({
          id: s.id,
          title: s.title,
          messages: [],
          messageCount: s.message_count ?? 0,
          updatedAt: s.updated_at,
        }))
        // 无历史会话时自动创建默认会话，保证首屏可直接发消息并持久化
        if (!sessions.value.length) {
          const s = await api.createSession('新对话')
          sessions.value.push({ id: s.id, title: s.title, messages: [], messageCount: 0, updatedAt: s.updated_at })
        }
        currentId.value = sessions.value[0].id
      } catch (e) {
        console.warn('会话加载失败，降级为本地新会话', e)
        sessions.value = [{ id: localId(), title: '新对话', messages: [], messageCount: 0, updatedAt: Date.now() }]
        currentId.value = sessions.value[0].id
      }
    },
    async getMessages(): Promise<any[]> {
      const s = sessions.value.find((x) => x.id === currentId.value)
      if (!s) return []
      if (s.messages.length) return s.messages
      try {
        s.messages = await api.getMessages(s.id)
      } catch {
        s.messages = []
      }
      return s.messages
    },
    async setMessages(msgs: any[]): Promise<void> {
      const s = sessions.value.find((x) => x.id === currentId.value)
      if (!s) return
      s.messages = msgs
      s.messageCount = msgs.length
      s.updatedAt = Date.now()
      try { await api.saveMessages(s.id, msgs) } catch (e) { console.warn('保存消息失败', e) }
    },
    async newSession(): Promise<string> {
      try {
        const s = await api.createSession('新对话')
        sessions.value.unshift({ id: s.id, title: s.title, messages: [], messageCount: 0, updatedAt: s.updated_at })
        currentId.value = s.id
        return s.id
      } catch (e) {
        console.warn('创建会话失败，降级为本地会话', e)
        const id = localId()
        sessions.value.unshift({ id, title: '新对话', messages: [], messageCount: 0, updatedAt: Date.now() })
        currentId.value = id
        return id
      }
    },
    async switchSession(id: string): Promise<void> {
      currentId.value = id
    },
    async deleteSession(id: string): Promise<void> {
      const i = sessions.value.findIndex((x) => x.id === id)
      if (i < 0) return
      sessions.value.splice(i, 1)
      if (currentId.value === id) {
        currentId.value = sessions.value[Math.max(0, i - 1)]?.id ?? ''
      }
      try { await api.deleteSession(id) } catch { /* 本地已移除 */ }
    },
    async setTitle(id: string, title: string): Promise<void> {
      const s = sessions.value.find((x) => x.id === id)
      if (!s) return
      if (!s.title || s.title === '新对话') s.title = title
      try { await api.renameSession(id, s.title) } catch { /* 忽略 */ }
    },
  }
}
