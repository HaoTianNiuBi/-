import { useEffect, useState } from "react";
import { Alert, App, Button, Checkbox, Image, Input, Modal, Popconfirm, Select, Space, Spin } from "antd";
import { ArrowLeftOutlined, ArrowRightOutlined, CloseOutlined, PictureOutlined } from "@ant-design/icons";
import { planningRequest, register } from "./api";
import type { CapsuleDetail, CapsuleImage, CapsuleKind, CapsuleList, Decision, PlanningHome } from "./api";
import { DateField } from "./DateField";
import { uid } from "./store";
import type { Library } from "./store";
import "./planning.css";

export type PlanningView = {
  kind: "decisions" | "capsules"; library: string | null; id?: string; version?: number; create?: boolean;
};

const capsuleLabels = { letter: "未来信", snapshot: "当下快照" };
const dateTime = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });
const errorText = (error: unknown) => error instanceof Error ? error.message : "操作失败，请重试";
const capsulePath = (id: string) => `capsules/${encodeURIComponent(id)}`;
const capsuleImageUrl = (library: string, capsuleId: string, imageId: string) =>
  `/api/planning/${encodeURIComponent(library)}/${capsulePath(capsuleId)}/images/${encodeURIComponent(imageId)}`;
const decisionPath = (id: string) => `decisions/${encodeURIComponent(id)}`;
const capsuleImageTypes = ["image/jpeg", "image/png", "image/webp"];
const maxCapsuleImages = 4;
type DraftImage = CapsuleImage & { dataUrl?: string };

function readImage(file: File): Promise<DraftImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const unreadable = () => reject(new Error("无法读取所选图片"));
    reader.onload = () => {
      const dataUrl = String(reader.result);
      const image = new window.Image();
      image.src = dataUrl;
      void image.decode().then(() => resolve({ id: uid(), name: file.name.slice(0, 150), dataUrl }), unreadable);
    };
    reader.onerror = unreadable;
    reader.readAsDataURL(file);
  });
}

function localDay() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function usePlanning<T>(library: string | null, libraries: Library[], path: string, revision: number, poll = false, keepPrevious = false) {
  const [state, setState] = useState<{ library: string; path: string; data?: T; error?: string }>();
  useEffect(() => {
    if (!library) return;
    let alive = true;
    let loading = false;
    async function load() {
      if (loading || !library) return;
      loading = true;
      try {
        await register(libraries);
        const data = await planningRequest<T>(library, path);
        if (alive) setState({ library, path, data });
      } catch (error) {
        if (alive) setState({ library, path, error: errorText(error) });
      } finally { loading = false; }
    }
    void load();
    const timer = poll ? window.setInterval(load, 30000) : undefined;
    if (poll) window.addEventListener("focus", load);
    return () => {
      alive = false;
      if (timer !== undefined) window.clearInterval(timer);
      if (poll) window.removeEventListener("focus", load);
    };
  }, [library, libraries, path, revision, poll]);
  return state?.library === library && (state?.path === path || keepPrevious) ? state : undefined;
}

export function PlanningCards({ library, libraries, revision, disabled, onOpen }: {
  library: string | null; libraries: Library[]; revision: number; disabled: boolean;
  onOpen: (view: PlanningView) => void;
}) {
  const data = usePlanning<PlanningHome>(library, libraries, "home", revision, true)?.data;
  if (!library || !data) return null;
  const { capsules, decisions } = data;
  if (!capsules.length && !decisions.length) return null;
  return <section className="planning-home" aria-label="计划与回看">
    {capsules.length > 0 && <div className="followup-card">
        <h2>时间胶囊到期</h2><p>你有 {capsules.length} 封胶囊可以回看。</p>
        <Button size="small" type="primary" disabled={disabled} onClick={() => onOpen({ kind: "capsules", library, id: capsules[0].id })}>回看胶囊</Button>
      </div>}
    {decisions.length > 0 && <div className="followup-card">
        <h2>待复盘</h2><p>{decisions[0].title}{decisions.length > 1 && ` 等 ${decisions.length} 项`}</p>
        <Button size="small" disabled={disabled} onClick={() => onOpen({ kind: "decisions", library, id: decisions[0].id })}>填写结果</Button>
      </div>}
  </section>;
}

type PanelProps = { library: string; libraries: Library[]; revision: number; onChanged: () => void };

function DecisionEditor({ record, onSave, onBack, busy }: {
  record?: Decision; onSave: (value: unknown) => Promise<void>; onBack: () => void; busy: boolean;
}) {
  const [value, setValue] = useState(() => ({ id: record?.id || uid(), version: record?.version || 0,
    title: record?.title || "", choice: record?.choice || "", options: record?.options || "", reason: record?.reason || "",
    expectation: record?.expectation || "", due: record?.due || "", result: record?.result || "" }));
  const fields = [
    ["choice", "最后的选择", 500], ["options", "考虑过的选项", 1000], ["reason", "为什么这样选", 1000],
    ["expectation", "当时的预期", 1000], ["result", "实际结果", 1500],
  ] as const;
  return <form className="planning-form" onSubmit={(event) => { event.preventDefault(); void onSave({ ...value, due: value.due || null }); }}>
    <label>标题<Input autoFocus required maxLength={100} value={value.title} onChange={(e) => setValue({ ...value, title: e.target.value })} /></label>
    {fields.slice(0, 4).map(([key, label, limit]) => <label key={key}>{label}
      <Input.TextArea rows={2} required={key === "choice"} maxLength={limit} value={value[key]} onChange={(e) => setValue({ ...value, [key]: e.target.value })} />
    </label>)}
    <DateField label="复盘日期" value={value.due} onChange={(due) => setValue({ ...value, due })} />
    <label>实际结果<Input.TextArea rows={2} maxLength={1500} value={value.result} onChange={(e) => setValue({ ...value, result: e.target.value })} /></label>
    {record?.original && <details><summary>聊天原话</summary><blockquote>{record.original}</blockquote></details>}
    <Space><Button onClick={onBack} disabled={busy}>返回</Button>
      <Button htmlType="submit" type="primary" loading={busy} disabled={!value.title.trim() || !value.choice.trim()}>保存决定</Button></Space>
  </form>;
}

function Decisions({ library, libraries, revision, onChanged, initialId, create }: PanelProps & { initialId?: string; create?: boolean }) {
  const state = usePlanning<Decision[]>(library, libraries, "decisions", revision);
  const [id, setId] = useState(initialId);
  const [editing, setEditing] = useState(Boolean(create));
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  const record = state?.data?.find((item) => item.id === id);
  async function write<T>(path: string, method: string, body?: unknown, finished?: (result: T) => void) {
    setBusy(true);
    try {
      const result = await planningRequest<T>(library, path, method, body);
      finished?.(result);
      onChanged();
    } catch (error) { message.error(errorText(error)); }
    finally { setBusy(false); }
  }
  if (state?.error) return <Alert type="error" message={state.error} action={<Button size="small" onClick={onChanged}>重试</Button>} />;
  if (!state?.data) return <Spin />;
  if (editing) return <DecisionEditor record={record} busy={busy} onBack={() => setEditing(false)}
    onSave={(value) => write<Decision>("decisions", "PUT", value, (saved) => { setId(saved.id); setEditing(false); })} />;
  if (record) return <article className="planning-detail">
    <Button type="text" onClick={() => setId(undefined)} disabled={busy}>返回决定列表</Button>
    <h3>{record.title}</h3>
    {record.draft ? <p className="planning-muted">聊天草稿</p> : record.due && <p className="planning-muted">{record.due} 复盘</p>}
    <dl>{([["最后的选择", record.choice], ["考虑过的选项", record.options], ["理由", record.reason],
      ["当时的预期", record.expectation], ["实际结果", record.result], ["AI 复盘", record.review]] as const)
      .filter(([, value]) => value).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {record.original && <details><summary>聊天原话</summary><blockquote>{record.original}</blockquote></details>}
    <Space wrap>
      <Button type="primary" disabled={busy} onClick={() => setEditing(true)}>{record.draft ? "完善草稿" : record.result ? "编辑决定" : "填写结果"}</Button>
      {!record.draft && record.result && !record.review && <Button loading={busy} onClick={() => void write(`${decisionPath(record.id)}/review`, "POST", { version: record.version })}>AI 辅助复盘</Button>}
      <Popconfirm title="删除这项决定？" okText="删除" cancelText="取消" onConfirm={() => write(`${decisionPath(record.id)}?version=${record.version}`, "DELETE", undefined, () => setId(undefined))}>
        <Button type="text" danger disabled={busy}>删除</Button>
      </Popconfirm>
    </Space>
  </article>;
  return <div className="planning-list">
    <Button type="primary" onClick={() => { setId(undefined); setEditing(true); }}>新建决定</Button>
    {id && <p className="planning-muted">这项决定已不可用。</p>}
    {!state.data.length && <p className="planning-muted">还没有决定</p>}
    {state.data.map((item) => <button type="button" className="planning-row" key={item.id} onClick={() => setId(item.id)}>
      <strong>{item.title}</strong><span>{item.draft ? "草稿" : item.result ? "已填结果" : item.due ? `${item.due} 复盘` : "待填结果"}</span>
    </button>)}
  </div>;
}

function CapsuleMark({ kind }: { kind: CapsuleKind }) {
  return <span className={`capsule-mark capsule-mark--${kind}`} aria-hidden="true"><i /></span>;
}

function CapsulePhotos({ images, library, capsuleId, onRemove }: {
  images: DraftImage[]; library: string; capsuleId: string; onRemove?: (id: string) => void;
}) {
  if (!images.length) return null;
  return <div className="capsule-photos" aria-label="胶囊图片">
    <Image.PreviewGroup>
      {images.map((image) => <figure key={image.id}>
        <Image src={image.dataUrl || capsuleImageUrl(library, capsuleId, image.id)} alt={image.name} />
        {onRemove && <Button type="text" shape="circle" size="small" icon={<CloseOutlined />}
          className="capsule-photo-remove" aria-label={`移除图片 ${image.name}`} onClick={() => onRemove(image.id)} />}
      </figure>)}
    </Image.PreviewGroup>
  </div>;
}

function CapsuleEditor({ library, kind, record, onSave, onBack, busy }: {
  library: string; kind: CapsuleKind; record?: CapsuleDetail; onSave: (value: unknown) => Promise<void>; onBack: () => void; busy: boolean;
}) {
  const [value, setValue] = useState(() => ({ id: record?.id || uid(), version: record?.version || 0,
    kind, title: record?.title || "", body: record?.body || "",
    due: record?.due || "", sealed: record ? Boolean(record.sealed) : true, reminder: record ? Boolean(record.reminder) : true }));
  const [images, setImages] = useState<DraftImage[]>(record?.versions[0]?.images ?? []);
  const [reading, setReading] = useState(false);
  const { message } = App.useApp();
  const canSave = !busy && !reading && Boolean(value.title.trim() && value.due && (value.body.trim() || images.length));
  async function addImages(files: File[]) {
    if (!files.length) return;
    if (files.length + images.length > maxCapsuleImages) { message.warning("每封胶囊最多添加 4 张图片"); return; }
    if (files.some((file) => !capsuleImageTypes.includes(file.type) || !file.size || file.size > 3 * 1024 * 1024)) {
      message.warning("请选择 3 MB 以内的 JPG、PNG 或 WebP 图片");
      return;
    }
    setReading(true);
    try {
      const added = await Promise.all(files.map(readImage));
      setImages((current) => [...current, ...added]);
    }
    catch (error) { message.error(errorText(error)); }
    finally { setReading(false); }
  }
  return <form className="capsule-editor" onSubmit={(event) => {
    event.preventDefault();
    if (canSave) void onSave({ ...value, images: images.map((image) => image.dataUrl
      ? { name: image.name, data: image.dataUrl.split(",", 2)[1] } : image.id) });
  }}>
    <div className={`capsule-paper capsule-paper--${kind}`}>
      <div className="capsule-paper-heading"><CapsuleMark kind={kind} /><h3>{capsuleLabels[kind]}</h3></div>
      <label className="capsule-field">标题<Input autoFocus required variant="borderless" maxLength={100}
        value={value.title} onChange={(e) => setValue({ ...value, title: e.target.value })} /></label>
      <label className="capsule-field">{kind === "letter" ? "写给未来的自己" : "此刻的目标、想法和预期"}
        <Input.TextArea rows={5} variant="borderless" maxLength={4000} value={value.body}
          onChange={(e) => setValue({ ...value, body: e.target.value })} />
      </label>
      <CapsulePhotos images={images} library={library} capsuleId={value.id}
        onRemove={busy || reading ? undefined : (id) => setImages((current) => current.filter((image) => image.id !== id))} />
      <div className="capsule-attachment-actions">
        <label className="capsule-upload"><PictureOutlined aria-hidden="true" /> {reading ? "正在读取图片" : "添加图片"}
          <input className="sr-only" type="file" aria-label="添加图片" accept={capsuleImageTypes.join(",")} multiple
            disabled={busy || reading || images.length >= maxCapsuleImages} onChange={(event) => {
              const files = Array.from(event.currentTarget.files ?? []);
              event.currentTarget.value = "";
              void addImages(files);
            }} />
        </label>
        <span>{images.length} / {maxCapsuleImages}</span>
      </div>
    </div>
    <div className="capsule-options">
      <DateField label="回看日期" className="capsule-field capsule-date" required
        min={record && record.due < localDay() ? record.due : localDay()} value={value.due}
        onChange={(due) => setValue({ ...value, due })} />
      <Space wrap size={12}><Checkbox checked={value.sealed} onChange={(e) => setValue({ ...value, sealed: e.target.checked })}>封存正文</Checkbox>
        <Checkbox checked={value.reminder} onChange={(e) => setValue({ ...value, reminder: e.target.checked })}>到期提醒</Checkbox></Space>
    </div>
    <div className="capsule-actions"><Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack} disabled={busy}>返回</Button>
      <Button htmlType="submit" type="primary" loading={busy} disabled={!canSave}>保存胶囊</Button></div>
  </form>;
}

function Capsules({ library, libraries, revision, onChanged, initialId, initialVersion, onDiscuss }: PanelProps & {
  initialId?: string; initialVersion?: number; onDiscuss: (library: string, text: string) => void;
}) {
  const [id, setId] = useState(initialId);
  const [version, setVersion] = useState(initialVersion);
  const [editing, setEditing] = useState<CapsuleKind>();
  const [previewDay, setPreviewDay] = useState("");
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  const list = usePlanning<CapsuleList>(id ? null : library, libraries,
    `capsules${previewDay ? `?preview_day=${encodeURIComponent(previewDay)}` : ""}`, revision, false, true);
  const detail = usePlanning<CapsuleDetail>(id ? library : null, libraries, id ? capsulePath(id) : "capsules", revision);
  const record = detail?.data;
  const selected = record?.versions.find((item) => item.version === version) ?? record?.versions[0];
  const state = id ? detail : list;
  async function write<T>(path: string, method: string, data?: unknown, finished?: (result: T) => void) {
    setBusy(true);
    try {
      const result = await planningRequest<T>(library, path, method, data);
      finished?.(result);
      onChanged();
    } catch (error) { message.error(errorText(error)); }
    finally { setBusy(false); }
  }
  if (editing) return <CapsuleEditor library={library} kind={editing} record={record} busy={busy} onBack={() => setEditing(undefined)}
    onSave={(value) => write<{id: string}>("capsules", "PUT", value, (saved) => { setId(saved.id); setVersion(undefined); setEditing(undefined); })} />;
  if (state?.error) return <Alert type="error" message={state.error} action={<Button size="small" onClick={id ? () => setId(undefined) : onChanged}>{id ? "返回列表" : "重试"}</Button>} />;
  if (!state?.data) return <Spin />;
  if (record && selected) return <article className="capsule-detail">
    <div className="capsule-actions">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setId(undefined)} disabled={busy}>返回列表</Button>
      <Select aria-label="胶囊版本" value={selected.version} onChange={setVersion}
        options={record.versions.map((item) => ({ value: item.version, label: `第 ${item.version} 版 · ${dateTime.format(item.saved)}` }))} />
    </div>
    <div className={`capsule-paper capsule-paper--${record.kind}`} key={selected.version}>
      <div className="capsule-paper-heading"><CapsuleMark kind={record.kind} /><span>{capsuleLabels[record.kind]}</span></div>
      <h3>{selected.title}</h3>
      <p className="capsule-meta"><time dateTime={selected.due}>{selected.due}</time> 回看{record.seen ? " · 已回看" : ""}</p>
      {selected.body && <p className="capsule-body">{selected.body}</p>}
      <CapsulePhotos images={selected.images ?? []} library={library} capsuleId={record.id} />
    </div>
    <Space wrap>
      {record.dueNow && <Button type="primary" disabled={busy} onClick={() => void write(`${capsulePath(record.id)}/act`, "POST", { version: record.version, action: "seen" })}>标为已回看</Button>}
      {record.dueNow && <Button disabled={busy} onClick={() => void write(`${capsulePath(record.id)}/act`, "POST", { version: record.version, action: "snooze" })}>明天提醒</Button>}
      <Button disabled={busy} onClick={() => setEditing(record.kind)}>编辑最新版</Button>
      {record.body && <Button type="text" disabled={busy} onClick={() => onDiscuss(library, `请陪我回看时间胶囊「${record.title}」的最新记录。`)}>聊聊这封胶囊</Button>}
      <Popconfirm title="删除胶囊及它的全部版本？" okText="删除" cancelText="取消"
        onConfirm={() => write(`${capsulePath(record.id)}?version=${record.version}`, "DELETE", undefined, () => setId(undefined))}>
        <Button type="text" danger disabled={busy}>删除</Button>
      </Popconfirm>
    </Space>
  </article>;
  return <div className="capsule-shelf">
    <div className="capsule-choices" aria-label="新建胶囊">
      {(["letter", "snapshot"] as const).map((kind) => <button type="button" key={kind} className={`capsule-choice capsule-choice--${kind}`}
        onClick={() => setEditing(kind)}>
        <CapsuleMark kind={kind} />
        <strong>{kind === "letter" ? "写一封未来信" : "留一张当下快照"}</strong>
        <ArrowRightOutlined aria-hidden="true" />
      </button>)}
    </div>
    {!!list?.data?.items.length && <section className="capsule-collection" aria-label="已保存的时间胶囊">
      <h3>已保存</h3>
      {list.data.preview && <Alert type="info" message={`模拟 ${list.data.day} · 预览不改变提醒状态`} />}
      {list.data.items.map((item) => <button type="button" className="capsule-card" key={item.id} onClick={() => { setVersion(undefined); setId(item.id); }}>
        <CapsuleMark kind={item.kind} />
        <span className="capsule-card-text"><strong>{item.title}</strong>
          <span className="capsule-meta">{capsuleLabels[item.kind]} · <time dateTime={item.due}>{item.due}</time>{item.dueNow ? " · 到期" : item.seen ? " · 已回看" : ""}</span>
          {item.body && <span className="capsule-excerpt">{item.body}</span>}</span>
        <ArrowRightOutlined aria-hidden="true" />
      </button>)}
      <details className="capsule-preview"><summary>预览到期提醒</summary>
        <DateField label="模拟日期" className="capsule-field capsule-date" value={previewDay} onChange={setPreviewDay} />
        {previewDay && <Button type="text" size="small" onClick={() => setPreviewDay("")}>结束预览</Button>}
      </details>
    </section>}
  </div>;
}

export default function PlanningPanel({ view, libraries, revision, onChanged, onClose, onDiscuss }: {
  view: PlanningView; libraries: Library[]; revision: number; onChanged: () => void; onClose: () => void;
  onDiscuss: (library: string, text: string) => void;
}) {
  const [library, setLibrary] = useState<string | null>(view.library);
  const sameLibrary = library === view.library;
  const isCapsule = view.kind === "capsules";
  const title = isCapsule ? "时间胶囊" : "决策复盘";
  const props = library ? { library, libraries, revision, onChanged } : null;
  return <Modal open title={title} onCancel={onClose} footer={null} width={isCapsule ? 620 : 560} centered
    rootClassName={isCapsule ? "planning-modal capsule-modal" : "planning-modal"}>
    <label className="planning-library">记忆库<Select aria-label="记忆库" value={library ?? undefined} placeholder="选择记忆库" onChange={setLibrary}
      options={libraries.map((item) => ({ value: item.id, label: item.name }))} /></label>
    {props && (isCapsule
      ? <Capsules key={library} {...props} initialId={sameLibrary ? view.id : undefined} initialVersion={sameLibrary ? view.version : undefined} onDiscuss={onDiscuss} />
      : <Decisions key={library} {...props} initialId={sameLibrary ? view.id : undefined} create={sameLibrary && view.create} />)}
  </Modal>;
}
