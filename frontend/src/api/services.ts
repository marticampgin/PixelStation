import { post, put, remove, request } from './client';
import type {
  Conversation,
  ConversationDetail,
  LocalFile,
  Memory,
  MemoryDetail,
  ModelsResponse,
  Settings,
} from '../types';

export const core = {
  conversations: (q = '', archived = false) =>
    request<Conversation[]>(`/conversations?q=${encodeURIComponent(q)}&archived=${archived}`),
  conversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  createConversation: () => post<Conversation>('/conversations', {}),
  updateConversation: (id: string, body: { title?: string; archived?: boolean }) =>
    request<Conversation>(`/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteConversation: (id: string) => remove(`/conversations/${id}?confirmed=true`),
  files: (q = '') => request<LocalFile[]>(`/files?q=${encodeURIComponent(q)}`),
  upload: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<LocalFile>('/files/upload', { method: 'POST', body });
  },
  deleteFile: (id: string) => remove(`/files/${id}?confirmed=true`),
  createFile: (filename: string, content: string, format: string) =>
    post<LocalFile>('/files/create', { filename, content, format }),
  memories: (q = '', category = '') =>
    request<Memory[]>(
      `/memory?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`,
    ),
  memory: (id: string) => request<MemoryDetail>(`/memory/${id}`),
  createMemory: (body: Partial<Memory>) => post<Memory>('/memory', body),
  updateMemory: (id: string, body: Partial<Memory>) =>
    request<Memory>(`/memory/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteMemory: (id: string) => remove(`/memory/${id}?confirmed=true`),
  settings: () => request<Settings>('/settings'),
  saveSettings: (settings: Settings) => put<Settings>('/settings', settings),
  models: () => request<ModelsResponse>('/models'),
};
