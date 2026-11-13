import { useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";
import { Alert, App, Button, Input, InputNumber, Modal, Popconfirm, Select, Space, Spin } from "antd";
import { ArrowLeftOutlined, LeftOutlined, PlusOutlined, RightOutlined } from "@ant-design/icons";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { readArchive, writeArchive } from "./api";
import type { Archive, CheckInRecord, CognitiveChange, Memory, Tracker } from "./api";
import { DateField } from "./DateField";
import { uid } from "./store";
import type { Library } from "./store";
import "./archive.css";

gsap.registerPlugin(useGSAP);

export type ArchiveTab = "profile" | "timeline" | "tracking" | "changes";
const leafTabs = [
  { id: "timeline", label: "时间线" },
  { id: "tracking", label: "跟踪" },
  { id: "changes", label: "认知变化" },
] as const;
const twigPositions = [
  [47.6, 63, -16], [23.7, 55, -15], [67, 64.1, 14],
  [34.9, 55.5, 17], [79.1, 78, -17], [9.4, 42.4, -19],
  [58.4, 65.2, -12], [89.2, 68.5, 11], [16, 52, 20],
] as const;
const facetOrder = ["name", "learning", "challenges", "communication", "decisions", "habits", "planning", "interests", "collaboration"];
const sprigFocus = [64, 38, 54, 42];
const sprigStem = [[48.2, 96.2], [48.4, 96.2], [48.8, 91.9], [48.8, 91.9]];
const mobileLabelLift = [0, 0, 0, 16, 17, 30, 23, 0, 17];
const tabLabels: Record<ArchiveTab, string> = { profile: "画像", timeline: "时间线", tracking: "跟踪", changes: "认知变化" };
type TrackerForm =
  | { kind: "create"; id: string }
  | { kind: "target"; tracker: Tracker }
  | { kind: "checkin"; tracker: Tracker; id: string }
  | { kind: "edit"; tracker: Tracker; entry: CheckInRecord };
type Mutation = (path: string, method: "POST" | "PUT" | "PATCH", body: Record<string, unknown>) => Promise<boolean>;

const dateLabel = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" });
const timeLabel = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const amountLabel = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 });
const relationship = /家人|父亲|母亲|父母|哥哥|姐姐|弟弟|妹妹|朋友|室友|同学|队友|老师|同事|伴侣|男友|女友|恋爱/;
const correctionLabels: Record<string, string> = {
  edit: "修改了记忆", change_goal: "改了主意", goal_changed: "改了目标", reject: "否认了记忆", record_wrong: "否认了记忆",
};
const kindLabels: Record<string, string> = { fact: "个人记录", preference: "偏好", goal: "目标" };
const trackerPath = (id: string) => `/trackers/${encodeURIComponent(id)}`;
const entryPath = (tracker: string, entry: string) => `${trackerPath(tracker)}/entries/${encodeURIComponent(entry)}`;

function isoDay(date = new Date()) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function showDay(day: string) {
  return dateLabel.format(new Date(`${day}T12:00:00`));
}

function resultText(entry: CheckInRecord, unit: string) {
  return entry.outcome === "missed" ? "明确未完成" : `完成 ${amountLabel.format(entry.amount ?? 0)} ${unit}`;
}

function Original({ text }: { text: string }) {
  return <details className="archive-original"><summary>查看原话</summary><blockquote>{text}</blockquote></details>;
}

function LeafSurface({ children }: { children?: ReactNode }) {
  return (
    <div className="archive-macro-stage">
      <div className="archive-macro-art" aria-hidden="true">
        <img src="/assets/scene/archive-leaf-macro.png" alt="" />
      </div>
      {children}
    </div>
  );
}

function MemoryRow({ item }: { item: Memory }) {
  return (
    <li className="archive-record">
      <p>{item.content}</p>
      <div className="archive-row-meta">
        <time>{dateLabel.format(item.sourceMessage.created)}</time>
        {item.status === "superseded" && <span>曾经的记录</span>}
        <Original text={item.sourceMessage.text} />
      </div>
    </li>
  );
}

function Profile({ data }: { data: Archive }) {
  const active = data.memories.filter((item) => item.status === "active");
  const relations = active.filter((item) => item.kind === "fact" && relationship.test(item.content));
  const groups = [
    { label: "个人记录", items: active.filter((item) => item.kind === "fact" && !relations.includes(item)) },
    { label: "偏好与沟通", items: active.filter((item) => item.kind === "preference") },
    { label: "当前目标", items: active.filter((item) => item.kind === "goal") },
    { label: "关系", items: relations },
  ].filter((group) => group.items.length);
  return (
    <div className="archive-profile">
      <div className="archive-name"><span>使用栖语的第 {data.relation.days} 天</span></div>
      {groups.length ? groups.map(({ label, items }) => (
        <section className="archive-section" key={label}>
          <h3>{label}</h3>
          <ul>{items.map((item) => <MemoryRow item={item} key={item.id} />)}</ul>
        </section>
      )) : <p className="archive-empty">这个记忆库还没有可用记录。</p>}
      {data.relation.corrections.length > 0 && (
        <section className="archive-section">
          <h3>最近的纠正</h3>
          <ul>{data.relation.corrections.map((correction) => (
            <li className="archive-record" key={correction.id}>
              {correction.content && <p>{correction.content}</p>}
              <span className="archive-row-meta">{dateLabel.format(correction.created)} · {correctionLabels[correction.action]}</span>
            </li>
          ))}</ul>
        </section>
      )}
    </div>
  );
}

function Timeline({ data, onDecision }: { data: Archive; onDecision: (id?: string, create?: boolean) => void }) {
  const [selectedDay, setSelectedDay] = useState<string>();
  const [pagesBack, setPagesBack] = useState(0);
  const ordinary = data.memories.filter((item) => !data.changes.some((change) =>
    change.after.created === item.sourceMessage.created && change.after.text === item.sourceMessage.text && change.after.quote === item.content,
  ));
  const rows: { id: string; day: string; time: number; label: string; text: string; source: string; decisionId?: string }[] = [
    ...ordinary.map((item) => ({
      id: item.id, day: isoDay(new Date(item.sourceMessage.created)), time: item.sourceMessage.created,
      label: item.status === "active" ? kindLabels[item.kind] || "记忆" : "曾经的记录",
      text: item.content, source: item.sourceMessage.text,
    })),
    ...data.trackers.flatMap((tracker) => tracker.entries.filter((entry) => entry.status === "active").map((entry) => ({
      id: entry.id, day: entry.day, time: entry.sources[0].created,
      label: "跟踪", text: `${tracker.name} · ${resultText(entry, tracker.unit)}`,
      source: entry.sources.map((source) => source.text).filter(Boolean).join("\n"),
    }))),
    ...data.changes.map((change) => ({
      id: change.id, day: isoDay(new Date(change.after.created)), time: change.after.created,
      label: "认知变化", text: change.after.quote, source: change.after.text,
    })),
    ...(data.decisions || []).flatMap((decision) => [
      { id: `decision-${decision.id}`, day: isoDay(new Date(decision.created)), time: decision.created,
        label: decision.draft ? "决定草稿" : "决定", text: decision.title, source: "", decisionId: decision.id },
      ...(decision.result_at ? [{ id: `result-${decision.id}`, day: isoDay(new Date(decision.result_at)), time: decision.result_at,
        label: "决定结果", text: `${decision.title} · ${decision.result}`, source: "", decisionId: decision.id }] : []),
    ]),
  ].sort((a, b) => b.day.localeCompare(a.day) || b.time - a.time).slice(0, 50).reverse();
  const actions = <Space className="archive-planning-actions" wrap>
    <Button size="small" type="primary" onClick={() => onDecision(undefined, true)}>新建决定</Button>
    <Button size="small" onClick={() => onDecision()}>全部决定</Button>
  </Space>;
  const grouped = new Map<string, typeof rows>();
  for (const row of rows) {
    const group = grouped.get(row.day) || [];
    group.push(row);
    grouped.set(row.day, group);
  }
  if (!rows.length) return <><LeafSurface><p className="archive-leaf-empty">有记录后，日期会出现在叶脉上。</p></LeafSurface>{actions}</>;
  const days = Array.from(grouped.keys());
  const pageSize = 6;
  const maxBack = Math.ceil(days.length / pageSize) - 1;
  const offset = Math.min(maxBack, pagesBack);
  const end = days.length - offset * pageSize;
  const visibleDays = days.slice(Math.max(0, end - pageSize), end);
  const activeDay = selectedDay && visibleDays.includes(selectedDay) ? selectedDay : visibleDays[visibleDays.length - 1];
  const veins = [20.3, 32.8, 45.5, 56.1, 67.5, 79.4];
  return (
    <>
      <LeafSurface>
        {visibleDays.map((day, index) => {
          const position = veins[Math.floor((veins.length - visibleDays.length) / 2) + index];
          return (
            <button type="button" key={day} className="archive-vein-date"
              style={{ "--vein-x": `${position}%`, "--vein-side": index % 2 === 0 ? -1 : 1 } as CSSProperties}
              aria-label={showDay(day)} aria-pressed={day === activeDay} onClick={() => setSelectedDay(day)}>
              <i aria-hidden="true" /><time dateTime={day}>{day.slice(5).replace("-", ".")}</time>
            </button>
          );
        })}
        {maxBack > 0 && <div className="archive-vein-pages">
          <button type="button" aria-label="更早日期" disabled={offset === maxBack} onClick={() => setPagesBack(offset + 1)}><LeftOutlined /></button>
          <button type="button" aria-label="更晚日期" disabled={offset === 0} onClick={() => setPagesBack(offset - 1)}><RightOutlined /></button>
        </div>}
      </LeafSurface>
      {actions}
      <section className="archive-leaf-records" aria-label={showDay(activeDay)}>
        <h2><time dateTime={activeDay}>{showDay(activeDay)}</time></h2>
        <ul>{grouped.get(activeDay)?.map((item) => (
            <li className="archive-record" key={item.id}>
              <span className="archive-row-meta">{item.label}</span>
              <p>{item.text}</p>
              {item.source && <Original text={item.source} />}
              {item.decisionId && <Button type="link" size="small" onClick={() => onDecision(item.decisionId)}>查看决定</Button>}
            </li>
        ))}</ul>
      </section>
    </>
  );
}

function Changes({ items, busy, mutate }: { items: CognitiveChange[]; busy: boolean; mutate: Mutation }) {
  const [selected, setSelected] = useState<string>();
  const index = Math.max(0, items.findIndex((item) => item.id === selected));
  const item = items[index];
  if (!item) return <LeafSurface><p className="archive-leaf-empty">认知变化会在叶脉两侧展开。</p></LeafSurface>;
  return (
    <div className="archive-changes">
      <LeafSurface>
        <div className="archive-change-face archive-change-before"><strong>之前</strong><span>{item.before.quote}</span></div>
        <div className="archive-change-face archive-change-after"><strong>现在</strong><span>{item.after.quote}</span></div>
        {items.length > 1 && <div className="archive-vein-pages">
          <button type="button" aria-label="较新的变化" disabled={index === 0} onClick={() => setSelected(items[index - 1].id)}><LeftOutlined /></button>
          <button type="button" aria-label="较早的变化" disabled={index === items.length - 1} onClick={() => setSelected(items[index + 1].id)}><RightOutlined /></button>
        </div>}
      </LeafSurface>
      <article className="archive-leaf-records">
        <div className="archive-change-evidence">
          {([["之前", item.before], ["现在", item.after]] as const).map(([label, evidence]) => (
            <div key={label}>
              <div className="archive-row-meta"><strong>{label}</strong>
                <time dateTime={new Date(evidence.created).toISOString()}>{dateLabel.format(evidence.created)} {timeLabel.format(evidence.created)}</time>
              </div>
              <blockquote>{evidence.quote}</blockquote>
              {evidence.text !== evidence.quote && <Original text={evidence.text} />}
            </div>
          ))}
        </div>
        <Space wrap size={4}>
          <Button size="small" type="text" disabled={busy} onClick={() => void mutate(`/changes/${item.id}`, "PATCH", { action: "dismissed" })}>这不是变化</Button>
          <Button size="small" type="text" disabled={busy} onClick={() => void mutate(`/changes/${item.id}`, "PATCH", { action: "different_context" })}>情境不同</Button>
        </Space>
      </article>
    </div>
  );
}

function EntryRow({ entry, tracker, busy, mutate, edit }: {
  entry: CheckInRecord; tracker: Tracker; busy: boolean; mutate: Mutation; edit: () => void;
}) {
  const [selected, setSelected] = useState<string>();
  const candidates = tracker.entries.filter((item) => item.status === "active" && item.day === entry.day && item.outcome === entry.outcome);
  const match = candidates.find((item) => item.id === selected) || (candidates.length === 1 ? candidates[0] : undefined);
  const canMerge = match?.amount === entry.amount && Boolean(match);
  function act(action: "merge" | "separate" | "revoke") {
    void mutate(entryPath(tracker.id, entry.id), "PATCH", { action, version: entry.version, into: match?.id });
  }
  return (
    <li className={`archive-checkin${entry.status === "pending" ? " pending" : ""}`}>
      <div className="archive-entry-line">
        <div>
          <strong>{resultText(entry, tracker.unit)}</strong>
          <div className="archive-row-meta">
            <time dateTime={entry.day}>{showDay(entry.day)}</time>
            {Array.from(new Set(entry.sources.map((source) => source.channel))).map((channel) => (
              <span key={channel}>{channel === "chat" ? "聊天提取" : "手动打卡"}</span>
            ))}
          </div>
        </div>
        <Space size={2} wrap>
          {entry.outcome === "done" && <Button type="text" size="small" onClick={edit} disabled={busy}>改数量</Button>}
          <Popconfirm title="撤销这条记录？" okText="撤销" cancelText="返回" onConfirm={() => act("revoke")}>
            <Button type="text" size="small" disabled={busy}>撤销</Button>
          </Popconfirm>
        </Space>
      </div>
      {entry.sources.some((source) => source.text) && (
        <details className="archive-original">
          <summary>查看来源</summary>
          {entry.sources.map((source) => (
            <div className="archive-source" key={source.id}>
              <span className="archive-row-meta">{source.channel === "chat" ? "聊天提取" : "手动打卡"} · {dateLabel.format(source.created)} {timeLabel.format(source.created)}</span>
              <blockquote>{source.text || "手动记录"}</blockquote>
            </div>
          ))}
        </details>
      )}
      {entry.status === "pending" && (
        <div className="archive-duplicate">
          <strong>{candidates.length ? "和已有记录是同一次吗？" : "这条记录尚未计入"}</strong>
          {candidates.length > 1 && <Select size="small" aria-label="选择已有记录" placeholder="选择已有记录" value={match?.id} onChange={setSelected}
            options={candidates.map((item) => ({ value: item.id, label: `${resultText(item, tracker.unit)} · ${timeLabel.format(item.sources[0].created)}` }))} />}
          {match && <span className="archive-row-meta">已有记录：{resultText(match, tracker.unit)}{match.amount !== entry.amount && " · 数量一致后可合并"}</span>}
          <Space wrap>
            {candidates.length > 0 && <Button size="small" type="primary" disabled={busy || !canMerge} onClick={() => act("merge")}>同一次，合并</Button>}
            <Button size="small" disabled={busy} onClick={() => act("separate")}>{candidates.length ? "另一次，计入" : "计入记录"}</Button>
          </Space>
        </div>
      )}
    </li>
  );
}

function Tracking({ trackers, busy, mutate, openForm }: {
  trackers: Tracker[]; busy: boolean; mutate: Mutation; openForm: (form: TrackerForm) => void;
}) {
  const [chosen, setChosen] = useState<string>();
  const tracker = trackers.find((item) => item.id === chosen) || trackers.find((item) =>
    item.entries.some((entry) => entry.status === "pending")) || trackers[0];
  const today = tracker?.entries.filter((entry) =>
    entry.status === "active" && entry.day === isoDay() && entry.goal === tracker.goal.id) || [];
  const completed = today.reduce((total, entry) => total + (entry.amount || 0), 0);
  const progress = tracker ? Math.min(100, (completed / tracker.goal.amount) * 100) : 0;
  const status = completed > 0 ? `今日 ${amountLabel.format(completed)} / ${tracker?.goal.amount} ${tracker?.unit}`
    : today.length ? "明确未完成" : "今日未记录";
  const add = () => openForm({ kind: "create", id: uid() });
  return (
    <div className="archive-tracking">
      <LeafSurface>
        {tracker ? <>
          <div className="archive-tracker-focus">
            {trackers.length > 1 ? <Select aria-label="跟踪项" className="archive-inline-select" value={tracker.id} onChange={setChosen}
              options={trackers.map((item) => ({ value: item.id, label: item.name }))} /> : <strong>{tracker.name}</strong>}
            <span>{status}</span>
          </div>
          <div className="archive-progress-vein" role="progressbar" aria-label={`${tracker.name}今日进度`}
            aria-valuenow={Math.round(progress)} style={{ "--progress": `${progress}%` } as CSSProperties} />
        </> : <div className="archive-leaf-empty">
          <Button icon={<PlusOutlined />} onClick={add} disabled={busy}>新建跟踪</Button>
        </div>}
      </LeafSurface>
      {tracker && <div className="archive-leaf-records">
        <div className="archive-entry-line">
          <h3>言行对照 <span>最近 7 天</span></h3>
          <Button icon={<PlusOutlined />} onClick={add} disabled={busy}>新建跟踪</Button>
        </div>
        <article className="archive-tracker">
          <div className="archive-entry-line">
            <div>
              <h3>{tracker.name}</h3>
              <span className="archive-row-meta">每日目标 {amountLabel.format(tracker.goal.amount)} {tracker.unit}</span>
            </div>
            <Space wrap size={6}>
              <Button size="small" disabled={busy} onClick={() => openForm({ kind: "target", tracker })}>调整目标</Button>
              <Button size="small" type="primary" disabled={busy} onClick={() => openForm({ kind: "checkin", tracker, id: uid() })}>打卡</Button>
            </Space>
          </div>
          <div className="archive-segments">
            {[...tracker.segments].reverse().map((segment) => (
              <div className="archive-segment" key={segment.id}>
                <div className="archive-entry-line">
                  <strong>每日 {amountLabel.format(segment.amount)} {tracker.unit}</strong>
                  <span className="archive-row-meta">{showDay(segment.fromDay)}{segment.fromDay !== segment.toDay && ` 至 ${showDay(segment.toDay)}`}{tracker.segments.length > 1 && segment.fromDay === isoDay(new Date(segment.starts)) && ` · ${timeLabel.format(segment.starts)}起`}</span>
                </div>
                <div className="archive-segment-numbers">
                  <span>完成 <b>{amountLabel.format(segment.completed)}</b> {tracker.unit}（{segment.doneCount} 条）</span>
                  <span>计划 <b>{segment.plannedDays}</b> 天</span>
                  <span>未记录 <b>{segment.unrecordedDays}</b> 天</span>
                  {segment.missedCount > 0 && <span>明确未完成 <b>{segment.missedCount}</b> 条</span>}
                </div>
              </div>
            ))}
          </div>
          {tracker.entries.length > 0 && <ul className="archive-checkins">
            {[...tracker.entries].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending")).map((entry) => (
              <EntryRow entry={entry} tracker={tracker} busy={busy} mutate={mutate} key={entry.id}
                edit={() => openForm({ kind: "edit", tracker, entry })} />
            ))}
          </ul>}
        </article>
      </div>}
    </div>
  );
}

function ArchiveForm({ form, busy, mutate, close }: {
  form: TrackerForm; busy: boolean; mutate: Mutation; close: () => void;
}) {
  const [name, setName] = useState("");
  const [unit, setUnit] = useState("次");
  const [day, setDay] = useState(isoDay());
  const [amount, setAmount] = useState<number | null>(
    form.kind === "edit" ? form.entry.amount : form.kind === "target" ? form.tracker.goal.amount : 1,
  );
  const [outcome, setOutcome] = useState<"done" | "missed">("done");
  const [note, setNote] = useState("");
  const titles = { create: "新建跟踪", checkin: "打卡", target: "调整每日目标", edit: "修改完成数量" };
  const needsAmount = form.kind !== "checkin" || outcome === "done";
  const valid = (!needsAmount || (amount !== null && amount > 0))
    && (form.kind !== "create" || Boolean(name.trim() && unit.trim()))
    && (form.kind !== "checkin" || Boolean(day));
  async function submit() {
    if (!valid || busy) return;
    let saved: boolean;
    switch (form.kind) {
      case "create":
        saved = await mutate("/trackers", "POST", { id: form.id, name, unit, target: amount });
        break;
      case "target":
        saved = await mutate(`${trackerPath(form.tracker.id)}/target`, "PUT", { goal: form.tracker.goal.id, target: amount });
        break;
      case "checkin":
        saved = await mutate(`${trackerPath(form.tracker.id)}/entries`, "POST", { id: form.id, day, outcome, amount: needsAmount ? amount : null, note });
        break;
      case "edit":
        saved = await mutate(entryPath(form.tracker.id, form.entry.id), "PATCH", { action: "edit", version: form.entry.version, amount });
        break;
    }
    if (saved) close();
  }
  return (
    <Modal title={titles[form.kind]} open centered rootClassName="archive-form-modal" width={460}
      onCancel={close} onOk={() => void submit()} confirmLoading={busy}
      okText="保存" cancelText="取消" okButtonProps={{ disabled: !valid }}>
      <div className="archive-form">
        {form.kind === "create" && <>
          <label>跟踪什么<Input value={name} onChange={(event) => setName(event.target.value)} maxLength={40} placeholder="例如 跑步" autoFocus /></label>
          <label>单位<Input value={unit} onChange={(event) => setUnit(event.target.value)} maxLength={10} placeholder="例如 公里、分钟、次" /></label>
        </>}
        {form.kind === "checkin" && <>
          <DateField label="日期" required value={day} min={isoDay(new Date(form.tracker.created))} max={isoDay()} onChange={setDay} />
          <label>结果<Select value={outcome} onChange={setOutcome} options={[{ value: "done", label: "已完成" }, { value: "missed", label: "明确未完成" }]} /></label>
        </>}
        {needsAmount && <label>{form.kind === "create" || form.kind === "target" ? "每日目标" : "完成数量"}
          <InputNumber min={0.01} max={100000} value={amount} onChange={setAmount} addonAfter={form.kind === "create" ? unit : form.tracker.unit} />
        </label>}
        {form.kind === "checkin" && <label>备注（选填）<Input value={note} maxLength={280} onChange={(event) => setNote(event.target.value)} /></label>}
      </div>
    </Modal>
  );
}

export default function ArchivePanel({ initialLibrary, initialTab, libraries, name, revision, paused, onChanged, onClose, onDecision }: {
  initialLibrary: string | null; initialTab: ArchiveTab; libraries: Library[]; name: string;
  revision: number; paused: boolean; onChanged: () => void; onClose: () => void;
  onDecision: (library: string, id?: string, create?: boolean) => void;
}) {
  const [library, setLibrary] = useState(initialLibrary);
  const [tab, setTab] = useState<ArchiveTab>(initialTab);
  const [selected, setSelected] = useState<string | null>(initialTab === "profile" ? null : "name");
  const [state, setState] = useState<{ library: string; data?: Archive; error?: string }>();
  const [portraitError, setPortraitError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<TrackerForm>();
  const [branchReady, setBranchReady] = useState(false);
  const page = useRef<HTMLElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const sprigOrigin = useRef<{ x: number; y: number; angle: number }>(null);
  const leafTransition = useRef<gsap.core.Timeline>(null);
  const priorLeaf = useRef<string>(null);
  const visibleLeaves = useRef(new Set<string>());
  const { message } = App.useApp();
  const reduced = () => paused || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  useEffect(() => {
    if (!library) return;
    let active = true;
    setPortraitError(undefined);
    async function load() {
      if (!library) return;
      let data: Archive;
      try {
        data = await readArchive(library, libraries);
        if (!active) return;
        setState({ library, data });
      } catch (error) {
        if (active) setState({ library, error: error instanceof Error ? error.message : "无法读取档案" });
        return;
      }
      if (!data.portraitNeedsRefresh) return;
      try {
        await writeArchive(library, "/portrait/refresh", "POST", {});
        const refreshed = await readArchive(library, libraries);
        if (active) setState({ library, data: refreshed });
      } catch {
        if (active) setPortraitError(library);
      }
    }
    void load();
    return () => { active = false; };
  }, [library, libraries, revision]);
  const current = state?.library === library ? state : undefined;
  const data = current?.data;
  const portrait = data?.portrait || [];
  const trait = portrait.find((item) => item.facet === selected);
  const activeTab = selected ? tab : "profile";
  const branchVisible = selected === null;
  const leafSignature = portrait.map((item) => item.facet).join(",");

  useGSAP(() => {
    if (reduced()) return;
    gsap.from(".archive-page-bar", { autoAlpha: 0, y: -7, duration: .35, delay: .22 });
    gsap.from(".archive-branch-light", { opacity: 0, duration: .52, delay: .48 });
  }, { scope: page });
  useGSAP(() => {
    const element = page.current;
    if (!element) return;
    leafTransition.current?.kill();
    element.scrollTop = 0;
    const board = element.querySelector<HTMLElement>(".archive-branch-board");
    if (selected) {
      detailHeading.current?.focus({ preventScroll: true });
      const art = element.querySelector<HTMLElement>(".archive-macro-art");
      const origin = sprigOrigin.current;
      if (!reduced() && art && origin) {
        const frame = art.getBoundingClientRect();
        const branchFrame = board?.getBoundingClientRect();
        const camera = gsap.timeline({ defaults: { ease: "power2.inOut" } });
        leafTransition.current = camera;
        if (board && branchFrame) {
          gsap.killTweensOf(board);
          camera.to(board, { scale: 1.65, x: (frame.left + frame.width / 2 - origin.x) * .48,
            y: (frame.top + frame.height / 2 - origin.y) * .48, autoAlpha: 0, duration: .62,
            transformOrigin: `${origin.x - branchFrame.left}px ${origin.y - branchFrame.top}px` }, 0);
        }
        camera.fromTo(art, {
          x: origin.x - (frame.left + frame.width / 2), y: origin.y - (frame.top + frame.height / 2),
          scale: .09, rotation: origin.angle - 54, autoAlpha: 0,
        }, { x: 0, y: 0, scale: 1, rotation: 0, autoAlpha: 1, duration: .83, clearProps: "all" }, .13);
        camera.from(".archive-leaf-nav, .archive-leaf-caption, .archive-mobile-summary, .archive-leaf-records",
          { autoAlpha: 0, y: 8, duration: .36, clearProps: "all" }, .59);
      } else if (board) {
        gsap.set(board, { autoAlpha: 0 });
      }
    } else {
      const target = priorLeaf.current && element.querySelector<HTMLElement>(`[data-sprig="${priorLeaf.current}"]`);
      if (board) {
        gsap.killTweensOf(board);
        if (!priorLeaf.current && !reduced()) gsap.set(board, { autoAlpha: 0 });
        gsap.to(board, { x: 0, y: 0, scale: 1, autoAlpha: 1, duration: reduced() ? 0 : .55,
          delay: priorLeaf.current || reduced() ? 0 : .48,
          ease: "power2.out", clearProps: "transform,opacity,visibility" });
      }
      (target || element).focus({ preventScroll: true });
    }
  }, { scope: page, dependencies: [selected] });
  useGSAP(() => {
    if (!branchVisible) return;
    const leaves = page.current?.querySelectorAll<HTMLElement>("[data-trait-leaf] .archive-sprig-image");
    const next = new Set<string>();
    const grown: HTMLElement[] = [];
    for (const leaf of leaves || []) {
      const key = `${library}:${leaf.closest<HTMLElement>("[data-trait-leaf]")?.dataset.traitLeaf}`;
      next.add(key);
      if (!visibleLeaves.current.has(key)) grown.push(leaf);
    }
    if (grown.length && !reduced()) gsap.from(grown, {
      scale: .55, opacity: 0, duration: .6, stagger: .16, ease: "power2.out", clearProps: "transform,opacity",
    });
    visibleLeaves.current = next;
  }, { scope: page, dependencies: [leafSignature, branchVisible, library] });

  function openLeaf(event: MouseEvent<HTMLButtonElement>, leaf: string, angle: number) {
    const target = event.currentTarget;
    const box = (target.querySelector(".archive-sprig-focus") || target).getBoundingClientRect();
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    sprigOrigin.current = { x, y, angle };
    priorLeaf.current = leaf;
    setTab("profile");
    setSelected(leaf);
  }
  function returnToBranch() {
    sprigOrigin.current = null;
    setSelected(null);
    setTab("profile");
  }
  function changeLibrary(value: string) {
    setLibrary(value);
    sprigOrigin.current = null;
    if (selected) setSelected("name");
  }
  async function mutate(path: string, method: "POST" | "PUT" | "PATCH", body: Record<string, unknown>) {
    if (!library || busy) return false;
    setBusy(true);
    try {
      const result = await writeArchive(library, path, method, body);
      if (result.status === "pending") message.info("请核对是否和已有记录是同一次");
      try {
        setState({ library, data: await readArchive(library, libraries) });
      } catch {
        setState({ library, error: "已保存，暂时无法刷新档案" });
      }
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : "档案保存失败");
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function dismissTrait() {
    if (!trait) return;
    const saved = await mutate(`/portrait/${encodeURIComponent(trait.facet)}/dismiss`, "POST", {});
    if (saved) returnToBranch();
  }
  const branchLeaves = [{ facet: "name", title: name }, ...portrait];
  return (
    <main className={`archive-page${branchVisible ? " archive-branch-view" : " archive-leaf-view"}`}
      data-motion={paused ? "off" : undefined} ref={page} aria-label="档案" tabIndex={-1}>
      <header className="archive-page-bar">
        <div className="archive-page-title">
          <button className="archive-back" type="button" aria-label={branchVisible ? "返回" : "回到树枝"}
            title={branchVisible ? "返回" : "回到树枝"} onClick={branchVisible ? onClose : returnToBranch}><ArrowLeftOutlined /></button>
          {branchVisible ? <h1>档案</h1> : <span>档案</span>}
        </div>
        <Select className="archive-library"
          aria-label="档案记忆库" placeholder="选择记忆库" value={library || undefined} onChange={changeLibrary} disabled={busy || Boolean(form)}
          options={libraries.map((item) => ({ label: item.name, value: item.id }))} />
      </header>
      <div className="archive-branch-light" aria-hidden="true" />
      <div className="archive-branch-viewport">
        <div className="archive-branch-board" data-ready={branchReady} aria-label="画像枝条" inert={!branchVisible || !branchReady}>
            <img className="archive-branch-photo" src="/assets/scene/archive-branch-warm.png"
              alt="" draggable={false} onLoad={() => setBranchReady(true)} />
            {branchLeaves.map((leaf) => {
              const index = Math.max(0, facetOrder.indexOf(leaf.facet));
              const [x, y, angle] = twigPositions[index];
              const variant = index % 4;
              const [stemX, stemY] = sprigStem[variant];
              return (
                <div className="archive-twig" key={leaf.facet} style={{
                  "--x": `${x}%`, "--y": `${y}%`, "--angle": `${angle}deg`,
                  "--tile-x": variant % 2 ? "100%" : "0%", "--tile-y": variant >= 2 ? "100%" : "0%",
                  "--sprig-focus": `${sprigFocus[variant]}%`,
                  "--stem-x": `${stemX}%`, "--stem-y": `${stemY}%`,
                  "--mobile-label-lift": `${mobileLabelLift[index]}px`,
                } as CSSProperties}>
                  <button className="archive-sprig" type="button" data-sprig={leaf.facet}
                    data-trait-leaf={leaf.facet === "name" ? undefined : leaf.facet}
                    onClick={(event) => openLeaf(event, leaf.facet, angle)} title={leaf.title}>
                    <span className="archive-sprig-image" aria-hidden="true"><i className="archive-sprig-focus" /></span>
                    <span className="archive-sprig-label">{leaf.title}</span>
                  </button>
                </div>
              );
            })}
        </div>
      </div>
      {branchVisible && (current?.error || portraitError === library) &&
        <div className="archive-branch-error"><Alert type="error" message={current?.error || "暂时无法更新画像"}
          action={<Button size="small" onClick={onChanged}>重试</Button>} /></div>}
      {!branchVisible && (
        <div className="archive-reading">
          <nav className="archive-leaf-nav" aria-label="档案内容">
            {leafTabs.map((item) => <button type="button" key={item.id} aria-pressed={activeTab === item.id}
              onClick={() => setTab(activeTab === item.id ? "profile" : item.id)}>{item.label}</button>)}
          </nav>
          {activeTab === "profile" ? (
            <div>
              <LeafSurface>
                <div className={`archive-leaf-caption${trait ? " archive-trait-caption" : ""}`}>
                  <h1 ref={detailHeading} tabIndex={-1}>{trait?.title || name}</h1>
                  {trait && <p>{trait.summary}</p>}
                </div>
              </LeafSurface>
              {trait && <p className="archive-mobile-summary">{trait.summary}</p>}
              <section className="archive-leaf-records">
                {trait && data ? (
                  <>
                    <h2>依据</h2>
                    <ul>{trait.evidence.map((id) => {
                      const record = data.memories.find((item) => item.id === id);
                      return record ? <MemoryRow key={id} item={record} /> : null;
                    })}</ul>
                    <Popconfirm title="撤下这片画像叶？" okText="撤下" cancelText="返回" onConfirm={() => void dismissTrait()}>
                      <Button type="text" disabled={busy}>不符合我</Button>
                    </Popconfirm>
                  </>
                ) : data && <Profile data={data} />}
                {current?.error && <Alert type="error" showIcon message={current.error} action={<Button size="small" onClick={onChanged}>重试</Button>} />}
                {!library && <p className="archive-empty">选择一个记忆库</p>}
                {library && !current && <Spin />}
              </section>
            </div>
          ) : (
            <section className="archive-section-view" aria-label={tabLabels[activeTab]}>
              <h1 className="archive-visually-hidden" ref={detailHeading} tabIndex={-1}>{tabLabels[activeTab]}</h1>
              {current?.error && <Alert type="error" showIcon message={current.error} action={<Button size="small" onClick={onChanged}>重试</Button>} />}
              {library && !current && <Spin />}
              {!library && <p className="archive-empty">选择一个记忆库</p>}
              {!data && <LeafSurface />}
              {library && data && activeTab === "timeline" && <Timeline key={library} data={data} onDecision={(id, create) => onDecision(library, id, create)} />}
              {data && activeTab === "tracking" && <Tracking key={library} trackers={data.trackers} busy={busy} mutate={mutate} openForm={setForm} />}
              {data && activeTab === "changes" && <Changes key={library} items={data.changes} busy={busy} mutate={mutate} />}
            </section>
          )}
        </div>
      )}
      {form && <ArchiveForm form={form} busy={busy} mutate={mutate} close={() => setForm(undefined)} />}
    </main>
  );
}
