export type Mode = "共情" | "分析" | "挑战" | "自动";
export type Library = { id: string; name: string };
export type Entry = {
  id: string;
  role: "user" | "assistant" | "divider" | "notice";
  text: string;
  library: string | null;
  behavior?: string;
  challengeConfirmed?: boolean;
  provider?: string;
  model?: string;
  citations?: string[];
  webSources?: import("./api").WebSource[];
  webSourcesCited?: boolean;
  decisionDraftId?: string;
  capsuleRefs?: import("./api").CapsuleRef[];
  timestamp?: number;
  extraction?: string;
  memoryCount?: number;
  error?: string;
  reminderId?: string;
  reminderStatus?: import("./api").ReminderStatus;
  archive?: import("./api").ArchiveReceipt;
  request?: import("./api").TurnRequest;
};
export type Conversation = {
  id: string;
  title: string;
  library: string | null;
  mode: Mode;
  entries: Entry[];
  updated: number;
};
export type State = {
  libraries: Library[];
  conversations: Conversation[];
  deletedConversations: Conversation[];
  name: string;
  workspaceReset?: number;
  workspaceLineage?: string;
};
export type WorkspaceData = {
  workspace: string;
  lineage: string;
  reset: number;
  revision: number;
  conversations: string[];
  messages: string[];
  replies: string[];
  challenges: string[];
  memories: string[];
  updatedSources: string[];
};
const key = "qiyu.demo.v01";
export const workspaceDataKey = "qiyu.demo.data";
export const uid = () => crypto.randomUUID();
export const blank: State = {
  libraries: [
    { id: "life", name: "生活" },
    { id: "study", name: "学习" },
  ],
  conversations: [],
  deletedConversations: [],
  name: "体验者",
};

export function readWorkspaceData(): WorkspaceData | undefined {
  try {
    return JSON.parse(localStorage.getItem(workspaceDataKey) || "null") || undefined;
  } catch {
    return undefined;
  }
}

export function rememberWorkspaceData(data: WorkspaceData) {
  try {
    const saved = readWorkspaceData();
    if (saved && saved.lineage === data.lineage && (saved.reset > data.reset
        || (saved.workspace === data.workspace && saved.revision >= data.revision))) return saved;
    localStorage.setItem(workspaceDataKey, JSON.stringify(data));
  } catch {
    // The current tab can still apply erasure when storage is unavailable.
  }
  return data;
}

export function applyWorkspaceData(state: State, data = readWorkspaceData()): State {
  if (!data) return state;
  const workspace = { workspaceReset: data.reset, workspaceLineage: data.lineage };
  if ((!state.workspaceLineage || state.workspaceLineage === data.lineage)
      && data.reset > (state.workspaceReset ?? 0)) return { ...blank, ...workspace };
  const conversations = new Set(data.conversations);
  const messages = new Set(data.messages);
  const replies = new Set(data.replies);
  const challenges = new Set(data.challenges);
  const memories = new Set(data.memories);
  const updated = new Set(data.updatedSources);
  function clean(list: Conversation[]) {
    return list.filter((chat) => !conversations.has(chat.id)).map((chat) => {
      const entries = chat.entries.filter((entry) => !messages.has(entry.id) && !challenges.has(entry.id)
        && !(entry.id.startsWith("reply-") && (messages.has(entry.id.slice(6)) || replies.has(entry.id.slice(6))))
        && !entry.citations?.some((id) => memories.has(id))).map((entry) => {
        if (entry.role !== "user") return entry;
        if (replies.has(entry.id)) return { ...entry, request: undefined, extraction: undefined, error: undefined,
          memoryCount: undefined, archive: undefined, decisionDraftId: undefined };
        if (updated.has(entry.id)) return { ...entry, extraction: undefined, memoryCount: undefined };
        return entry;
      });
      const first = entries.find((entry) => entry.role === "user");
      const firstWasRemoved = chat.entries.find((entry) => entry.role === "user")?.id !== first?.id;
      return { ...chat, entries, title: firstWasRemoved ? (first?.text.slice(0, 24) || "新对话") : chat.title };
    });
  }
  return { ...state, ...workspace, conversations: clean(state.conversations), deletedConversations: clean(state.deletedConversations) };
}

export function load(): State {
  try {
    const value = JSON.parse(
      localStorage.getItem(key) || "null",
    ) as State | null;
    if (
      value &&
      Array.isArray(value.libraries) &&
      value.libraries.every(
        (l) => typeof l.id === "string" && typeof l.name === "string",
      ) &&
      Array.isArray(value.conversations) &&
      value.conversations.every(
        (c) =>
          typeof c.id === "string" &&
          Array.isArray(c.entries) &&
          ["共情", "分析", "挑战", "自动"].includes(c.mode),
      ) &&
      typeof value.name === "string"
    )
      return applyWorkspaceData({
        ...value,
        deletedConversations: Array.isArray(value.deletedConversations)
          ? value.deletedConversations : [],
        conversations: value.conversations.map((c) => ({
          ...c,
          entries: c.entries.map((e) =>
            e.extraction === "processing"
              ? { ...e, extraction: "failed", error: "上次处理中断，可重试" }
              : e,
          ),
        })),
      });
  } catch {
    /* Malformed or unavailable local storage: use an empty demo. */
  }
  return blank;
}
export function save(state: State) {
  localStorage.setItem(key, JSON.stringify(applyWorkspaceData(state)));
}
export const modes: Mode[] = ["共情", "分析", "挑战", "自动"];
export function reply(
  text: string,
  mode: Mode,
): { text: string; behavior: string } {
  const actual =
    mode === "自动"
      ? /难过|累|焦虑|烦|害怕|委屈/.test(text)
        ? "共情"
        : "分析"
      : mode;
  if (actual === "共情")
    return {
      behavior: "共情",
      text: "可以先停一停，把此刻的感受放在这里。你愿意从最让你在意的那一刻说起吗？\n\n这是一条固定的演示回复，仅用于体验共情模式的呈现，不代表真实模型理解。",
    };
  if (actual === "挑战")
    return {
      behavior: "挑战 · 尚未执行",
      text: "挑战会先核对过去的原话、现在的情况，以及你的目标有没有变化。\n\n本版尚未接入长期记忆与跨轮确认，因此不会判断你前后矛盾。你可以先切换到分析或共情模式体验界面。",
    };
  return {
    behavior: "分析",
    text: "我们可以从三个角度梳理：发生了什么、你最在意什么，以及哪些部分可以由你决定。\n\n你想先从哪一部分展开？\n\n这是一条固定的演示回复。本版不会检索其他会话或记忆库，也尚未接入真实模型。",
  };
}
