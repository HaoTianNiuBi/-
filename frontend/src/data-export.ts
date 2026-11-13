import type { CapsuleDetail, Decision, PortraitLeaf, TurnResult } from "./api";
import type { Entry, State } from "./store";

type ExportedMemory = {
  id: string;
  source: string;
  content: string;
  status: string;
  created: number;
};

export type LibraryExport = {
  workspace: string;
  revision: number;
  format: string;
  exportedAt: number;
  library: { id: string | null; name: string };
  messages: {
    id: string; cid: string; sid: string; text: string; created: number; trashed: number;
    reply?: Pick<TurnResult, "text" | "behavior" | "provider" | "model" | "citations" | "timestamp" | "webSources" | "capsuleRefs">;
  }[];
  memories: (ExportedMemory & { history: { action: string; created: number }[] })[];
  timeline: ExportedMemory[];
  portrait: PortraitLeaf[];
  trackers: {
    name: string; unit: string;
    entries: { day: string; status: string; outcome: string; amount: number | null; sources: { quote: string }[] }[];
  }[];
  decisions: Decision[];
  capsules: (Pick<CapsuleDetail, "title" | "versions"> & {
    attachments: { id: string; name: string; mime: string; base64: string }[];
  })[];
  changes: { old_content: string; quote: string; status: string; created: number }[];
  reminders: { content: string; due: number; status: string }[];
};

type ExportedEntry = Pick<Entry, "id" | "role" | "text" | "timestamp" | "behavior" | "provider"
  | "model" | "citations" | "webSources" | "capsuleRefs"> & { segment?: string };

function collectConversations(snapshot: LibraryExport, state: State) {
  const scope = snapshot.library.id;
  const conversations = new Map<string, { id: string; trashed: boolean; entries: Map<string, ExportedEntry> }>();
  function conversation(id: string, trashed: boolean) {
    let result = conversations.get(id);
    if (!result) {
      result = { id, trashed, entries: new Map() };
      conversations.set(id, result);
    }
    return result;
  }
  for (const [chats, trashed] of [[state.conversations, false], [state.deletedConversations, true]] as const) {
    for (const chat of chats) {
      for (const entry of chat.entries) {
        if (entry.library !== scope || entry.role === "divider") continue;
        const { id, role, text, timestamp, behavior, provider, model, citations, webSources, capsuleRefs } = entry;
        conversation(chat.id, trashed).entries.set(id, { id, role, text, timestamp, behavior, provider, model,
          citations, webSources, capsuleRefs, segment: entry.request?.segment });
      }
    }
  }
  for (const message of snapshot.messages) {
    const { entries } = conversation(message.cid, Boolean(message.trashed));
    entries.set(message.id, { id: message.id, role: "user", text: message.text, timestamp: message.created, segment: message.sid });
    if (message.reply) {
      const id = "reply-" + message.id;
      entries.set(id, { id, role: message.reply.provider === "local-reminder" ? "notice" : "assistant",
        ...message.reply, segment: message.sid });
    }
  }
  return Array.from(conversations.values(), ({ entries: stored, ...chat }) => {
    const entries = [...stored.values()].sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
    const title = entries.find((entry) => entry.role === "user")?.text.slice(0, 24) || "对话";
    return { ...chat, title, entries };
  });
}

const timestamp = (value: number) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const statusLabels: Record<string, string> = {
  active: "有效", rejected: "已否认", superseded: "已失效", paused: "暂停使用", expired: "已过期",
  pending: "待处理", done: "已完成", cancelled: "已取消", revoked: "已撤销", merged: "已合并", shown: "已记录",
  dismissed: "已撤下", different_context: "情境不同",
};
const roleLabels = { user: "我", assistant: "栖语", notice: "提醒", divider: "分段" };
const escape = (value: string) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replace(/[\\*_[\]\x60|#!]/g, "\\$&");
const quote = (value: string) => escape(value).split("\n").map((line) => "> " + line).join("\n");

function markdown(data: LibraryExport, conversations: ReturnType<typeof collectConversations>) {
  const lines = [`# 栖语 · ${escape(data.library.name)}`, timestamp(data.exportedAt)];
  const section = (title: string) => lines.push("", `## ${title}`, "");
  const messages = new Map(data.messages.map((message) => [message.id, message]));
  section("对话");
  for (const chat of conversations) {
    lines.push(`### ${escape(chat.title)}${chat.trashed ? "（最近删除）" : ""}`);
    for (const entry of chat.entries) {
      lines.push("", `**${roleLabels[entry.role]}**${entry.timestamp ? " · " + timestamp(entry.timestamp) : ""}`, quote(entry.text));
    }
    lines.push("");
  }
  section("记忆台账");
  for (const memory of data.memories) {
    lines.push(`- ${escape(memory.content)} · ${statusLabels[memory.status] || memory.status}`);
    const original = messages.get(memory.source);
    if (original) lines.push("", quote(original.text), "");
    if (memory.history.length) lines.push(`  纠正记录 ${memory.history.length} 条`);
  }
  section("时间线");
  for (const memory of data.timeline) lines.push(`- ${timestamp(memory.created)} · ${escape(memory.content)}`);
  section("档案");
  for (const leaf of data.portrait) lines.push(`- **${escape(leaf.title)}** ${escape(leaf.summary)}`);
  for (const change of data.changes) {
    lines.push(`- ${timestamp(change.created)} · ${escape(change.old_content)} → ${escape(change.quote)}`);
  }
  section("跟踪记录");
  for (const tracker of data.trackers) {
    lines.push(`### ${escape(tracker.name)}`);
    for (const entry of tracker.entries) {
      const outcome = entry.outcome === "done" ? `完成 ${entry.amount} ${escape(tracker.unit)}` : "明确未完成";
      lines.push(`- ${entry.day} · ${outcome} · ${statusLabels[entry.status] || entry.status}`);
      for (const source of entry.sources) if (source.quote) lines.push("", quote(source.quote), "");
    }
  }
  section("决策复盘");
  for (const decision of data.decisions) {
    lines.push(`### ${escape(decision.title)}`);
    for (const [label, value] of [
      ["选择", decision.choice], ["选项", decision.options], ["理由", decision.reason], ["预期", decision.expectation],
      ["复盘日期", decision.due], ["实际结果", decision.result], ["复盘", decision.review],
    ]) {
      if (value) lines.push("", `**${label}**`, quote(value));
    }
    lines.push("");
  }
  section("时间胶囊");
  for (const capsule of data.capsules) {
    lines.push(`### ${escape(capsule.title)}`);
    for (const version of capsule.versions) {
      lines.push("", `**第 ${version.version} 版** · ${version.due} 回看`);
      if (version.body) lines.push(quote(version.body));
      if (version.images.length) lines.push("图片 " + version.images.map((image) => escape(image.name)).join("、"));
    }
  }
  section("提醒");
  for (const reminder of data.reminders) {
    lines.push(`- ${timestamp(reminder.due)} · ${escape(reminder.content)} · ${statusLabels[reminder.status] || reminder.status}`);
  }
  return lines.join("\n") + "\n";
}

export function downloadLibrary(snapshot: LibraryExport, state: State, format: "json" | "md") {
  const conversations = collectConversations(snapshot, state);
  const { messages: _messages, workspace: _workspace, revision: _revision, ...records } = snapshot;
  const content = format === "json" ? JSON.stringify({ ...records, conversations }, null, 2) : markdown(snapshot, conversations);
  const mime = format === "json" ? "application/json" : "text/markdown";
  const url = URL.createObjectURL(new Blob([content], { type: mime + ";charset=utf-8" }));
  const link = document.createElement("a");
  const name = snapshot.library.name.replace(/[<>:"/\\|?*]/g, "-");
  link.href = url;
  link.download = `栖语-${name}-${new Date(snapshot.exportedAt).toISOString().slice(0, 10)}.${format}`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
