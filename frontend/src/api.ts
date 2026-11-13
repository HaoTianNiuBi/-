import type { Library, Mode, WorkspaceData } from "./store";
import type { LibraryExport } from "./data-export";
export type TurnRequest = {
  id: string;
  conversation: string;
  segment: string;
  library: string | null;
  text: string;
  mode: Mode;
  webSearch?: boolean;
};
export type WebSource = { number: number; title: string; url: string };
export type TurnResult = {
  provider: string;
  model?: string;
  text: string;
  behavior: string;
  citations: string[];
  extraction: string;
  created: string[];
  timestamp: number;
  reminderId?: string;
  archive?: ArchiveReceipt;
  webSources?: WebSource[];
  webSourcesCited?: boolean;
  decisionDraftId?: string;
  capsuleRefs?: CapsuleRef[];
};
export type ArchiveReceipt = { recorded: number; pending: number; changes: number; unavailable?: boolean };
export type Reminder = { id: string; content: string; due: number };
export type ReminderStatus = "pending" | "done" | "cancelled";
export type ReminderAction = "done" | "later" | "cancel";
export type Memory = {
  id: string;
  bid: string;
  content: string;
  kind: string;
  status: string;
  created: number;
  updated: number;
  expires: number;
  version: number;
  confidence: string;
  followup_allowed?: boolean;
  sourceMessage: { id: string; text: string; created: number; cid: string };
};
export type TrackerSource = { id: string; channel: "chat" | "manual"; text: string; quote: string; created: number };
export type CheckInRecord = {
  id: string; goal: string; day: string; amount: number | null; outcome: "done" | "missed";
  status: "active" | "pending"; version: number; sources: TrackerSource[];
};
export type TrackerGoal = { id: string; amount: number; starts: number };
export type TrackerSegment = TrackerGoal & {
  fromDay: string; toDay: string; plannedDays: number; unrecordedDays: number;
  completed: number; doneCount: number; missedCount: number;
};
export type Tracker = {
  id: string; name: string; unit: string; created: number; goal: TrackerGoal;
  entries: CheckInRecord[]; segments: TrackerSegment[];
};
export type CognitiveChange = {
  id: string;
  before: { quote: string; text: string; created: number };
  after: { quote: string; text: string; created: number };
};
export type Archive = {
  memories: Memory[]; trackers: Tracker[]; changes: CognitiveChange[];
  decisions: Decision[];
  portrait: PortraitLeaf[];
  portraitNeedsRefresh: boolean;
  relation: { days: number; corrections: { id: string; action: string; created: number; content: string | null }[] };
};
export type Decision = {
  id: string; title: string; choice: string; options: string; reason: string; expectation: string;
  due: string | null; result: string; result_at: number | null; review: string | null;
  draft: number; created: number; version: number; original: string | null; conversation: string | null;
};
export type CapsuleRef = { id: string; title: string; version: number; saved: number };
export type CapsuleKind = "letter" | "snapshot";
export type CapsuleImage = { id: string; name: string };
export type CapsuleVersion = {
  title: string; body: string; due: string; sealed: number; reminder: number; saved: number; version: number;
  images: CapsuleImage[];
};
export type Capsule = Omit<CapsuleVersion, "body" | "images"> & {
  id: string; kind: CapsuleKind; body?: string; dueNow: boolean; seen: number; created: number;
};
export type CapsuleDetail = Capsule & { body: string; versions: CapsuleVersion[] };
export type CapsuleList = { items: Capsule[]; day: string; preview: boolean };
export type PlanningHome = {
  capsules: Pick<Capsule, "id" | "kind" | "due" | "version">[];
  decisions: Pick<Decision, "id" | "title">[];
};
export type PortraitLeaf = { facet: string; title: string; summary: string; evidence: string[] };
export type Permissions = {
  health: boolean;
  finance: boolean;
  relationship: boolean;
};
export type Challenge = {
  id: string;
  library: string;
  segment: string;
  memory: Memory;
  text: string;
};
export type ChallengeDecision = "confirmed" | "record_wrong" | "situation_changed" | "goal_changed" | "skip";
export type ChallengeResult = {
  id: string;
  decision: ChallengeDecision;
  timestamp: number;
  text?: string;
  library?: string;
  model?: string;
  citations?: string[];
};
export type FollowupAction = "start" | "later" | "ignore" | "stop";
export type BackfillJob = {
  id: string;
  bid: string;
  category: keyof Permissions;
  status: "queued" | "running" | "completed" | "cancelled" | "failed";
  total: number;
  processed: number;
  recorded: number;
  failed: number;
};
let workspace: string | undefined;
export function useWorkspace(workspaceId: string) {
  workspace = workspaceId;
}

async function checkedFetch(url: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (workspace && !headers.has("X-Qiyu-Workspace")) headers.set("X-Qiyu-Workspace", workspace);
  const response = await fetch(url, {
    ...options,
    headers,
  });
  if (!response.ok) {
    if (response.status === 401) session = undefined;
    let detail = "本地服务暂不可用";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* Keep the concise connection error. */
    }
    throw new Error(detail);
  }
  return response;
}
async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  return (await checkedFetch(url, options)).json();
}
export async function readArchive(library: string, libraries: Library[]) {
  await register(libraries);
  return request<Archive>(`/api/archive?library_id=${encodeURIComponent(library)}`);
}
export async function writeArchive(
  library: string, path: string, method: "POST" | "PUT" | "PATCH", data: Record<string, unknown>,
) {
  await connect();
  return request<{ ok?: boolean; status?: string }>(`/api/archive/${encodeURIComponent(library)}${path}`, {
    method, body: JSON.stringify(data),
  });
}
export async function planningRequest<T>(library: string, path: string, method = "GET", data?: unknown) {
  await connect();
  return request<T>(`/api/planning/${encodeURIComponent(library)}/${path}`, {
    method, body: data === undefined ? undefined : JSON.stringify(data),
  });
}
let session: Promise<WorkspaceData> | undefined;
export async function connect() {
  if (!session)
    session = request<WorkspaceData>("/api/bootstrap", { method: "POST" }).then((data) => {
      useWorkspace(data.workspace);
      return data;
    }).catch((e) => {
      session = undefined;
      throw e;
    });
  return session;
}
export async function register(libraries: Library[]) {
  await connect();
  const headers = workspace ? { "X-Qiyu-Workspace": workspace } : undefined;
  await Promise.all(
    libraries.map((l) =>
      request("/api/libraries", { method: "PUT", body: JSON.stringify(l), headers }),
    ),
  );
  return headers;
}
type TurnEvent =
  | { type: "text"; text: string }
  | { type: "done"; result: TurnResult }
  | { type: "error"; detail: string };

export async function sendTurn(body: TurnRequest, libraries: Library[], onText: (text: string) => void) {
  const headers = await register(libraries);
  const response = await checkedFetch("/api/turn/stream", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const reader = response.body?.getReader();
  if (!reader) throw new Error("无法接收回复，请重试");
  const decoder = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error("回复连接中断，请重试");
      pending += decoder.decode(value, { stream: true });
      const lines = pending.split("\n");
      pending = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line) as TurnEvent;
        if (event.type === "text") onText(event.text);
        if (event.type === "error") throw new Error(event.detail);
        if (event.type === "done") return event.result;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

export async function readWorkspaceDataState() {
  await connect();
  return request<WorkspaceData>("/api/data/state", { cache: "no-store" });
}

export async function exportLibrary(id: string | null, libraries: Library[]) {
  const headers = await register(libraries);
  const query = id === null ? "" : `?library_id=${encodeURIComponent(id)}`;
  return request<LibraryExport>(`/api/data/export${query}`, { headers, cache: "no-store" });
}

export async function eraseMemory(id: string, version: number, scope: "memory" | "source", blockSource: boolean) {
  await connect();
  return request<WorkspaceData>(`/api/data/memories/${encodeURIComponent(id)}`, {
    method: "DELETE", body: JSON.stringify({ version, scope, blockSource }),
  });
}

export async function eraseConversation(id: string) {
  await connect();
  return request<WorkspaceData>(`/api/data/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function eraseWorkspace() {
  await connect();
  return request<WorkspaceData>("/api/data/workspace", { method: "DELETE" });
}
export async function readMemories(id: string, libraries: Library[]) {
  await register(libraries);
  return request<Memory[]>(
    `/api/memories?library_id=${encodeURIComponent(id)}`,
  );
}
export async function updateMemory(
  m: Memory,
  action: string,
  content?: string,
) {
  await connect();
  return request(`/api/memories/${m.id}`, {
    method: "PATCH",
    body: JSON.stringify({ action, content, version: m.version }),
  });
}
export async function readPermissions() {
  await connect();
  return request<Permissions>("/api/permissions");
}
export type ModelStatus = {
  provider: "deepseek";
  configured: boolean;
  model: string;
  keyEditable?: boolean;
};
export async function readModelStatus() {
  await connect();
  return request<ModelStatus>("/api/model");
}
export async function saveModelKey(apiKey: string) {
  await connect();
  return request<ModelStatus>("/api/model/key", {
    method: "PUT", body: JSON.stringify({apiKey}),
  });
}
export async function testModelConnection(search = false) {
  await connect();
  return request<{ok: boolean}>(search ? "/api/model/search/test" : "/api/model/test", {method: "POST"});
}
export async function setConversationDeleted(id: string, deleted: boolean) {
  await connect();
  return request(`/api/conversations/${encodeURIComponent(id)}/${deleted ? "trash" : "restore"}`, {
    method: "POST",
  });
}
export async function writePermissions(value: Permissions) {
  return request<Permissions>("/api/permissions", {
    method: "PUT",
    body: JSON.stringify(value),
  });
}
export async function readPendingChallenge(conversation: string, segment: string) {
  await connect();
  const query = new URLSearchParams({ conversation, segment });
  return request<Challenge | null>(`/api/challenges/pending?${query}`);
}
export async function leaveChallengeScope(conversation: string, segment: string) {
  await connect();
  const query = new URLSearchParams({ segment });
  return request(`/api/conversations/${encodeURIComponent(conversation)}/challenge-scope?${query}`, { method: "POST" });
}
export async function resolveChallenge(challenge: Challenge, decision: ChallengeDecision, checks: boolean[]) {
  await connect();
  return request<ChallengeResult>(`/api/challenges/${encodeURIComponent(challenge.id)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ segment: challenge.segment, decision, record_ok: checks[0], situation_same: checks[1], goal_same: checks[2] }),
  });
}
export async function setFollowup(memoryId: string, allowed: boolean) {
  await connect();
  return request(`/api/memories/${encodeURIComponent(memoryId)}/followup`, {
    method: "PUT", body: JSON.stringify({ allowed }),
  });
}
export async function readFollowups(libraryId: string, libraries: Library[]) {
  await register(libraries);
  return request<Memory[]>(`/api/followups?library_id=${encodeURIComponent(libraryId)}`);
}
export async function actOnFollowup(memoryId: string, action: FollowupAction) {
  await connect();
  return request<{ memory: Memory | null }>(`/api/followups/${encodeURIComponent(memoryId)}/act`, {
    method: "POST", body: JSON.stringify({ action }),
  });
}
export async function readReminders(libraryId: string | null) {
  await connect();
  const query = libraryId ? `?library_id=${encodeURIComponent(libraryId)}` : "";
  return request<Reminder[]>(`/api/reminders${query}`);
}
export async function actOnReminder(id: string, action: ReminderAction) {
  await connect();
  return request<{ status: ReminderStatus }>(`/api/reminders/${encodeURIComponent(id)}/act`, {
    method: "POST", body: JSON.stringify({ action }),
  });
}
export async function readBackfills(libraryId: string) {
  await connect();
  return request<BackfillJob[]>(`/api/backfills?library_id=${encodeURIComponent(libraryId)}`);
}
export async function startBackfill(library: string, category: keyof Permissions, start: number, end: number, libraries: Library[]) {
  await register(libraries);
  return request<BackfillJob>("/api/backfills", {
    method: "POST", body: JSON.stringify({ library, category, start, end }),
  });
}
export const extractionLabels: Record<string, string> = {
  processing: "记忆处理中",
  recorded: "已记入台账",
  no_match: "未形成记忆",
  duplicate: "已有同类记录",
  unbound: "未绑定记忆库",
  sensitive_skipped: "敏感内容未记忆",
  failed: "处理失败",
  unavailable: "记忆判断暂不可用",
  deferred: "本轮暂未入账",
};
