import { useEffect, useState } from "react";
import { App, Button, Dropdown, Progress, Space } from "antd";
import { actOnFollowup, readBackfills, readFollowups, readReminders, startBackfill } from "./api";
import type { BackfillJob, Challenge, ChallengeDecision, FollowupAction, Memory, Permissions, Reminder, ReminderAction } from "./api";
import { DateField } from "./DateField";
import type { Library } from "./store";

export const categoryLabels: Record<keyof Permissions, string> = { health: "健康", finance: "财务", relationship: "感情" };

export function ChallengePrompt({ challenge, busy, onSource, onDecide }: {
  challenge: Challenge;
  busy: boolean;
  onSource: () => void;
  onDecide: (decision: ChallengeDecision, checks: boolean[]) => Promise<void>;
}) {
  const { modal } = App.useApp();
  const isGoal = challenge.memory.kind === "goal";
  function decide(decision: ChallengeDecision) {
    void onDecide(decision, [false, false, false]);
  }
  function confirmAnalysis() {
    modal.confirm({
      title: "继续分析？",
      content: "请确认这条旧记录准确，现在的情况和原来的目标也都没变。",
      okText: "确认并分析",
      cancelText: "返回",
      onOk: () => { void onDecide("confirmed", [true, true, true]); },
    });
  }
  return (
    <section className="challenge-card" aria-label="待确认的旧记录">
      <strong>{isGoal ? "这个旧目标还适用吗？" : "这条旧记录还适用吗？"}</strong>
      <blockquote>{challenge.memory.content}</blockquote>
      <div className="workflow-meta">
        <time>{new Date(challenge.memory.sourceMessage.created).toLocaleDateString("zh-CN")}</time>
        <button className="workflow-link" onClick={onSource}>查看来源</button>
      </div>
      <Space wrap>
        {isGoal && <Button size="small" type="primary" disabled={busy} onClick={() => decide("goal_changed")}>我改主意了</Button>}
        <Button size="small" type={isGoal ? "default" : "primary"} disabled={busy} onClick={confirmAnalysis}>继续分析</Button>
        <Dropdown trigger={["click"]} disabled={busy} menu={{ items: [
          { key: "correct", label: "记录不准", onClick: onSource },
          { key: "changed", label: "情况变了", onClick: () => decide("situation_changed") },
          ...(!isGoal ? [{ key: "goal", label: "目标变了", onClick: () => decide("goal_changed") }] : []),
          { key: "skip", label: "先跳过", onClick: () => decide("skip") },
        ] }}>
          <Button size="small" type="text">其他处理</Button>
        </Dropdown>
      </Space>
    </section>
  );
}

export function ReminderCards({ library, libraries, revision, disabled, onChanged, onStart, onReminder }: {
  library: string | null;
  libraries: Library[];
  revision: number;
  disabled: boolean;
  onChanged: () => void;
  onStart: (memory: Memory) => void;
  onReminder: (id: string, action: ReminderAction) => Promise<void>;
}) {
  const [data, setData] = useState<{ library: string | null; memories: Memory[]; reminders: Reminder[] }>();
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  useEffect(() => {
    let live = true;
    let loading = false;
    async function load() {
      if (loading) return;
      loading = true;
      try {
        const memories = library ? await readFollowups(library, libraries) : [];
        const reminders = await readReminders(library);
        if (live) setData({ library, memories, reminders });
      } catch {
        if (live) setData(undefined);
      } finally {
        loading = false;
      }
    }
    void load();
    const timer = window.setInterval(load, 30_000);
    window.addEventListener("focus", load);
    return () => {
      live = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", load);
    };
  }, [library, libraries, revision]);
  async function act(memory: Memory, action: FollowupAction) {
    setBusy(true);
    try {
      const result = await actOnFollowup(memory.id, action);
      setData((previous) => previous && { ...previous, memories: previous.memories.filter((item) => item.id !== memory.id) });
      onChanged();
      if (result.memory) onStart(result.memory);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "提醒操作失败");
      onChanged();
    } finally {
      setBusy(false);
    }
  }
  async function actReminder(reminder: Reminder, action: ReminderAction) {
    setBusy(true);
    try {
      await onReminder(reminder.id, action);
      setData((previous) => previous && { ...previous, reminders: previous.reminders.filter((item) => item.id !== reminder.id) });
    } catch (error) {
      message.error(error instanceof Error ? error.message : "提醒操作失败");
      onChanged();
    } finally {
      setBusy(false);
    }
  }
  if (!data || data.library !== library) return null;
  const [reminder] = data.reminders;
  if (reminder) return (
    <section className="followup-card followup-list" aria-label="待办提醒">
      <h2>提醒</h2>
      <p>{reminder.content}</p>
      <Space size={6} wrap>
        <Button type="primary" size="small" disabled={busy || disabled} onClick={() => void actReminder(reminder, "done")}>完成</Button>
        <Button type="text" size="small" disabled={busy || disabled} onClick={() => void actReminder(reminder, "later")}>明天再提醒</Button>
      </Space>
    </section>
  );
  const [memory] = data.memories;
  if (!memory) return null;
  return (
    <section className="followup-card followup-list" aria-label="记忆提醒">
      <h2>记忆提醒</h2>
      <p>{memory.content}</p>
      <Space size={6} wrap>
        <Button type="primary" size="small" disabled={busy || disabled} onClick={() => void act(memory, "start")}>接着聊</Button>
        <Button type="text" size="small" disabled={busy || disabled} onClick={() => void act(memory, "later")}>以后再说</Button>
        <Button type="text" size="small" disabled={busy || disabled} onClick={() => void act(memory, "stop")}>不再提醒</Button>
      </Space>
    </section>
  );
}

const jobLabels: Record<BackfillJob["status"], string> = {
  queued: "等待处理", running: "回溯中", completed: "已完成", cancelled: "权限关闭，已停止", failed: "回溯失败",
};

export function HistoryBackfill({ library, libraries, category, enabled, onChanged }: {
  library: string;
  libraries: Library[];
  category: keyof Permissions;
  enabled: boolean;
  onChanged: () => void;
}) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<BackfillJob>();
  const [error, setError] = useState("");
  const active = job?.status === "queued" || job?.status === "running";
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    async function load() {
      try {
        const jobs = await readBackfills(library);
        const latest = jobs.find((item) => item.category === category);
        if (!live) return;
        setJob(latest);
        if (latest?.status === "queued" || latest?.status === "running") {
          timer = window.setTimeout(load, 1000);
        } else if (active) onChanged();
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : "无法读取回溯进度");
      }
    }
    void load();
    return () => { live = false; window.clearTimeout(timer); };
  }, [library, category, active, enabled, onChanged]);
  async function startJob() {
    if (!enabled || !start || !end || start > end) return;
    const lastDay = new Date(`${end}T00:00:00`);
    lastDay.setDate(lastDay.getDate() + 1);
    setBusy(true);
    setError("");
    try {
      const result = await startBackfill(library, category, new Date(`${start}T00:00:00`).getTime(), lastDay.getTime(), libraries);
      setJob(result);
      if (result.status === "completed") onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法开始回溯");
    } finally {
      setBusy(false);
    }
  }
  const name = libraries.find((item) => item.id === library)?.name || "当前记忆库";
  return (
    <div className="backfill-form">
      <strong>补记「{name}」里的{categoryLabels[category]}信息</strong>
      <div className="backfill-dates">
        <DateField label="开始日期" tone="sage" value={start} onChange={setStart} disabled={busy || active} />
        <DateField label="结束日期" tone="sage" value={end} min={start || undefined} onChange={setEnd} disabled={busy || active} />
      </div>
      <Button loading={busy} disabled={active || !enabled || !start || !end || start > end} onClick={() => void startJob()}>开始补记</Button>
      {!enabled && <small>开启{categoryLabels[category]}记忆后可继续。</small>}
      {error && <p role="alert">{error}</p>}
      {job && (
        <div role="status" className="backfill-status">
          <Progress percent={job.total ? Math.round(job.processed / job.total * 100) : 100} showInfo={false} size="small" />
          <span>{jobLabels[job.status]} · {job.processed}/{job.total} 条 · 新增 {job.recorded} 条{job.failed ? ` · ${job.failed} 条未能判断` : ""}</span>
        </div>
      )}
    </div>
  );
}
