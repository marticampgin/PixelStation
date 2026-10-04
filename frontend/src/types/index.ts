export type Page =
  'chat' | 'images' | 'files' | 'memory' | 'web' | 'gmail' | 'calendar' | 'games' | 'settings';
export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  summary: string;
}
export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  model: string | null;
  created_at: string;
  status: string;
  attachment_ids: string[];
  memory_ids: string[];
  traces: Record<string, unknown>[];
}
export interface ConversationDetail extends Conversation {
  messages: Message[];
}
export interface LocalFile {
  id: string;
  filename: string;
  size: number;
  source: string;
  extension: string;
  created_at: string;
  media_type: string;
  parse_status: string;
  parse_error?: string;
  parser?: string;
}
export interface Memory {
  id: string;
  text: string;
  category: string;
  scope: string;
  source_conversation_id?: string;
  source_message_id?: string;
  importance: number;
  confidence: number;
  pinned: boolean;
  tags: string[];
  created_at: string;
  updated_at: string;
  retrieval_score?: number;
  retrieval_reason?: string;
}
export interface MemoryDetail extends Memory {
  revisions: Record<string, unknown>[];
  links: Record<string, unknown>[];
}
export interface Model {
  name: string;
  capabilities: string[];
  size?: number;
}
export interface ModelsResponse {
  available: boolean;
  models: Model[];
  error?: string;
}
export interface Settings {
  roles: Record<string, string>;
  ollama_url: string;
  context_tokens: number;
  auto_memory: boolean;
  retrieval_count: number;
  searxng_url: string;
  comfyui_url: string;
  critic_enabled: boolean;
  max_steps: number;
  keep_alive: string;
  summary_turns: number;
  profile: string;
  harness_enabled: boolean;
  harness_interval_hours: number;
  [key: string]: unknown;
}
export interface IntegrationStatus {
  available: boolean;
  endpoint: string;
  message: string;
}
export interface WebSource {
  url: string;
  title: string;
  snippet: string;
  engine?: string;
  text?: string;
  fetch_error?: string;
}
export interface Workflow {
  id: string;
  name: string;
  bindings: Record<string, { node: string; input: string }>;
  defaults?: { width?: number; height?: number };
  created_at: string;
}
export interface GeneratedImage {
  id: string;
  prompt: string;
  seed: number;
  width: number;
  height: number;
  workflow_id: string;
  created_at: string;
  content_url: string;
}
export interface ImageJob {
  id: string;
  status: string;
  progress: number;
  images: GeneratedImage[];
  prompt?: string;
  seed?: number;
  width?: number;
  height?: number;
  workflow_id?: string;
  error?: string;
  handoff_warning?: string;
  remote_cleanup_required?: boolean;
  prompt_id?: string | null;
}
export interface GoogleStatus {
  configured: boolean;
  connected: boolean;
  message: string;
  scopes: string[];
  connections: Record<GoogleService, GoogleServiceStatus>;
}
export type GoogleService = 'gmail' | 'calendar';
export interface GoogleServiceStatus {
  service: GoogleService;
  configured: boolean;
  connected: boolean;
  message: string;
  scopes: string[];
  account: { label: string; email?: string; calendar_id?: string } | null;
  migration_required: boolean;
}
export interface EmailThread {
  id: string;
  subject: string;
  from: string;
  snippet: string;
  date: string;
}
export interface EmailMessage {
  id: string;
  subject: string;
  from: string;
  to: string;
  date: string;
  body: string;
  attachments: { filename: string; mime_type: string; size: number }[];
  message_id?: string;
  reply_to?: string;
  label_ids?: string[];
}
export interface Approval {
  id: string;
  action: string;
  payload: Record<string, unknown>;
  expires_at: string;
}
export interface CalendarEvent {
  id: string;
  summary?: string;
  description?: string;
  location?: string;
  start: { dateTime?: string; date?: string; timeZone?: string };
  end: { dateTime?: string; date?: string; timeZone?: string };
}
export interface PokerSeat {
  index: number;
  name: string;
  stack: number;
  bet: number;
  contribution: number;
  folded: boolean;
  all_in: boolean;
  hole: string[];
  personality: string;
}
export interface PokerState {
  id: string;
  hand_number: number;
  stage: string;
  board: string[];
  pot: number;
  dealer: number;
  actor: number | null;
  seats: PokerSeat[];
  legal_actions: { action: string; min?: number; max?: number; amount?: number }[];
  history: { seat: number; action: string; amount: number; stage: string }[];
  winners: { seat: number; amount: number; hand: string }[];
  completed: boolean;
  model_status?: string;
}
