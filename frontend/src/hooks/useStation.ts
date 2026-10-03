import { useCallback, useEffect, useRef, useState } from 'react';
import { core } from '../api/services';
import { errorMessage, post, streamChat } from '../api/client';
import { useLocalStorage } from './useLocalStorage';
import { usePanelState } from './usePanelState';
import type {
  Conversation,
  ConversationDetail,
  LocalFile,
  Memory,
  ModelsResponse,
  Page,
  Settings,
  WebSource,
} from '../types';

export function useStation() {
  const [page, setPageState] = useState<Page>('chat');
  const { compact, leftOpen, setLeftOpen, rightOpen, setRightOpen, closeCompactPanels } =
    usePanelState();
  const [conversationId, setConversationId] = useLocalStorage<string | null>('conversation', null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [hasHistory, setHasHistory] = useState(false);
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [models, setModels] = useState<ModelsResponse>({ available: false, models: [] });
  const [model, setModel] = useLocalStorage('model', '');
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [streaming, setStreaming] = useState('');
  const [status, setStatus] = useState('');
  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState<LocalFile[]>([]);
  const [webSources, setWebSources] = useState<WebSource[]>([]);
  const [retrievedMemories, setRetrievedMemories] = useState<Memory[]>([]);
  const abort = useRef<AbortController | null>(null);
  const activeId = useRef<string | null>(conversationId);
  const createdId = useRef<string | null>(null);
  const loadSequence = useRef(0);

  function setPage(next: Page) {
    setPageState(next);
    closeCompactPanels();
  }

  const refreshConversations = useCallback(async () => {
    const chats = await core.conversations('', showArchived);
    setConversations(chats);
    if (chats.length) setHasHistory(true);
  }, [showArchived]);
  const refreshModels = useCallback(async () => {
    setModels(await core.models());
  }, []);
  useEffect(() => {
    let active = true;
    Promise.allSettled([
      core.conversations(),
      core.models(),
      core.settings(),
      core.conversations('', true),
    ]).then((results) => {
      if (!active) return;
      if (results[0].status === 'fulfilled') {
        setConversations(results[0].value);
        if (results[0].value.length) setHasHistory(true);
      }
      if (results[3].status === 'fulfilled' && results[3].value.length) setHasHistory(true);
      if (results[1].status === 'fulfilled') setModels(results[1].value);
      if (results[2].status === 'fulfilled') {
        setSettings(results[2].value);
        if (!model) setModel(results[2].value.roles.primary_chat ?? '');
      }
      const failed = results.find((result) => result.status === 'rejected');
      if (failed?.status === 'rejected') setError(errorMessage(failed.reason));
    });
    return () => {
      active = false;
    };
    // Bootstrap preferences only once, including StrictMode's cleanup.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    void refreshConversations().catch((err) => setError(errorMessage(err)));
  }, [refreshConversations]);

  useEffect(() => {
    const sequence = ++loadSequence.current;
    if (!conversationId) {
      setConversation(null);
      setLoadingChat(false);
      return;
    }
    if (createdId.current === conversationId) {
      createdId.current = null;
      setLoadingChat(false);
      return;
    }
    setLoadingChat(true);
    core
      .conversation(conversationId)
      .then((value) => {
        if (sequence === loadSequence.current) setConversation(value);
      })
      .catch((err) => {
        if (sequence !== loadSequence.current) return;
        if (err.status === 404) {
          setConversationId(null);
          setConversation(null);
        } else setError(errorMessage(err));
      })
      .finally(() => {
        if (sequence === loadSequence.current) setLoadingChat(false);
      });
  }, [conversationId]);

  function newChat() {
    if (busy) cancel();
    activeId.current = null;
    setConversationId(null);
    setConversation(null);
    setAttachments([]);
    setWebSources([]);
    setDraft('');
    setStreaming('');
    setRetrievedMemories([]);
    setPage('chat');
  }
  function openChat(id: string) {
    if (busy) cancel();
    activeId.current = id;
    setConversationId(id);
    setPage('chat');
    setAttachments([]);
    setWebSources([]);
    setDraft('');
    setRetrievedMemories([]);
  }
  function beginChat(content: string, files: LocalFile[] = [], sources: WebSource[] = []) {
    newChat();
    setDraft(content);
    setAttachments(files);
    setWebSources(sources.slice(0, 4));
  }

  async function send(regenerate = false) {
    if (busy || (!regenerate && !draft.trim())) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setError(null);
    setStreaming('');
    setStatus('Preparing response…');
    const content = draft.trim();
    const selectedFiles = attachments;
    const selectedSources = webSources;
    let id = conversationId;
    try {
      if (!id) {
        const created = await core.createConversation();
        id = created.id;
        activeId.current = id;
        createdId.current = id;
        setConversationId(id);
        setConversation({ ...created, messages: [] });
      }
      if (!regenerate) {
        setDraft('');
        setAttachments([]);
        setWebSources([]);
        setConversation((previous) =>
          previous
            ? {
                ...previous,
                messages: [
                  ...previous.messages,
                  {
                    id: `pending-${Date.now()}`,
                    conversation_id: id!,
                    role: 'user',
                    content,
                    model,
                    created_at: new Date().toISOString(),
                    status: 'pending',
                    attachment_ids: selectedFiles.map((file) => file.id),
                    memory_ids: [],
                    traces: [],
                  },
                ],
              }
            : previous,
        );
      }
      await streamChat(
        `/conversations/${id}/${regenerate ? 'regenerate' : 'messages'}`,
        regenerate
          ? { model: model || undefined }
          : {
              content,
              model: model || undefined,
              attachment_ids: selectedFiles.map((file) => file.id),
              web_sources: selectedSources.map((source) => ({
                url: source.url,
                title: source.title,
              })),
            },
        controller.signal,
        (event) => {
          if (event.type === 'token') {
            setStatus('');
            setStreaming((previous) => previous + (event.content ?? ''));
          }
          if (event.type === 'status') {
            setStatus(String(event.detail ?? event.stage ?? 'Working…'));
            if (Array.isArray(event.memories)) setRetrievedMemories(event.memories as Memory[]);
          }
          if (event.type === 'error') throw new Error(event.error ?? 'Generation failed.');
        },
      );
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) setError(errorMessage(err));
    } finally {
      setBusy(false);
      setStatus('');
      setStreaming('');
      abort.current = null;
      if (id && activeId.current === id)
        await core
          .conversation(id)
          .then((value) => {
            if (activeId.current === id) setConversation(value);
          })
          .catch((err) => setError(errorMessage(err)));
      await refreshConversations().catch((err) => setError(errorMessage(err)));
    }
  }

  const feedback = async (id: string, feedback: string) => {
    await post(`/messages/${id}/feedback`, { feedback });
  };
  function cancel() {
    abort.current?.abort();
    if (activeId.current)
      void post(`/conversations/${activeId.current}/cancel`).catch((err) =>
        setError(errorMessage(err)),
      );
  }
  return {
    page,
    setPage,
    compact,
    closeCompactPanels,
    leftOpen,
    setLeftOpen,
    rightOpen,
    setRightOpen,
    conversationId,
    conversations,
    conversation,
    showArchived,
    setShowArchived,
    hasHistory,
    models,
    model,
    setModel,
    settings,
    setSettings,
    error,
    setError,
    busy,
    loadingChat,
    streaming,
    status,
    draft,
    setDraft,
    attachments,
    setAttachments,
    webSources,
    setWebSources,
    retrievedMemories,
    refreshConversations,
    refreshModels,
    newChat,
    openChat,
    beginChat,
    send,
    cancel,
    feedback,
  };
}
export type Station = ReturnType<typeof useStation>;
