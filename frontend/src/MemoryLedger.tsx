import { useEffect, useState } from "react";
import {
  App,
  Button,
  Collapse,
  Drawer,
  Empty,
  Input,
  Modal,
  Select,
  Spin,
  Switch,
  Tag,
} from "antd";
import {
  readMemories,
  updateMemory,
  readPermissions,
  writePermissions,
  setFollowup,
} from "./api";
import type { Memory, Permissions } from "./api";
import type { Library, WorkspaceData } from "./store";
import { categoryLabels, HistoryBackfill } from "./MemoryWorkflows";
import { EraseMemoryDialog } from "./DataPanel";
const statusNames: Record<string, string> = {
  active: "有效",
  rejected: "已否认",
  superseded: "已失效",
  paused: "暂停使用",
  expired: "已过期",
};
const date = (n: number) =>
  new Date(n).toLocaleString("zh-CN", { hour12: false });
type Props = {
  open: boolean;
  onClose: () => void;
  libraries: Library[];
  library: string | null;
  highlight?: string;
  revision: number;
  canErase: boolean;
  onChanged: () => void;
  onErased: (data: WorkspaceData) => void;
};
export default function MemoryLedger(props: Props) {
  const [backfill, setBackfill] = useState<{ library: string; category: keyof Permissions }>();
  const [erasing, setErasing] = useState<Memory>();
  const [selected, setSelected] = useState<string | null>(props.library),
    [items, setItems] = useState<Memory[]>([]),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [edit, setEdit] = useState<Memory | null>(null),
    [content, setContent] = useState(""),
    [busy, setBusy] = useState(false),
    [permissions, setPermissions] = useState<Permissions>({
      health: false,
      finance: false,
      relationship: false,
    });
  const { message } = App.useApp();
  useEffect(() => {
    if (props.open) setSelected(props.library);
  }, [props.open, props.library]);
  async function refresh() {
    if (!selected) {
      setItems([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [mem, p] = await Promise.all([
        readMemories(selected, props.libraries),
        readPermissions(),
      ]);
      setItems(mem);
      setPermissions(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (!props.open) return;
    let alive = true;
    setError("");
    setItems([]);
    if (!selected) return;
    setLoading(true);
    Promise.all([readMemories(selected, props.libraries), readPermissions()])
      .then(([m, p]) => {
        if (alive) {
          setItems(m);
          setPermissions(p);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [props.open, selected, props.revision, props.libraries]);
  async function act(m: Memory, action: string, value?: string) {
    setBusy(true);
    try {
      await updateMemory(m, action, value);
      setEdit(null);
      props.onChanged();
      await refresh();
      message.success(
        action === "edit"
          ? "记忆已修改"
          : action === "reject"
            ? "已否认，后续不再引用"
            : "已失效，保留历史",
      );
    } catch (e) {
      message.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function changePermission(key: keyof Permissions, value: boolean) {
    setBusy(true);
    try {
      const p = await writePermissions({ ...permissions, [key]: value });
      setPermissions(p);
      props.onChanged();
      await refresh();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "更新失败");
    } finally {
      setBusy(false);
    }
  }
  async function toggleFollowup(memory: Memory, allowed: boolean) {
    setBusy(true);
    try {
      await setFollowup(memory.id, allowed);
      setItems((previous) => previous.map((item) => item.id === memory.id ? { ...item, followup_allowed: allowed } : item));
      props.onChanged();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "更新提醒设置失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Drawer
        title="记忆台账"
        rootClassName="conversation-drawer memory-drawer"
        size={560}
        open={props.open}
        onClose={props.onClose}
      >
        <div className="ledger-toolbar">
          <Select
            aria-label="台账记忆库"
            classNames={{ popup: { root: "qiyu-select-menu--sage" } }}
            value={selected}
            disabled={busy || loading}
            placeholder="选择记忆库"
            onChange={setSelected}
            options={props.libraries.map((l) => ({
              value: l.id,
              label: l.name,
            }))}
          />
          <Button onClick={refresh} disabled={!selected || loading}>
            刷新
          </Button>
        </div>
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: "privacy",
              label: "敏感记忆",
              children: (
                <div className="sensitive-settings">
                  <p>
                    关闭后暂停记录和引用，重新开启可恢复有效记忆。
                  </p>
                  {(Object.keys(categoryLabels) as (keyof Permissions)[]).map(
                    (key) => (
                      <div className="privacy-row" key={key}>
                        <span>{categoryLabels[key]}</span>
                        {permissions[key] && selected && (
                          <Button type="text" size="small" disabled={busy || loading}
                            aria-label={`补记${categoryLabels[key]}旧聊天`}
                            onClick={() => setBackfill({ library: selected, category: key })}>补记旧聊天</Button>
                        )}
                        <Switch
                          aria-label={`允许${categoryLabels[key]}记忆`}
                          checked={permissions[key]}
                          disabled={busy}
                          onChange={(v) => changePermission(key, v)}
                        />
                      </div>
                    ),
                  )}
                  {backfill && selected === backfill.library && (
                    <HistoryBackfill key={`${backfill.library}:${backfill.category}`} library={backfill.library}
                      libraries={props.libraries} category={backfill.category} enabled={permissions[backfill.category]}
                      onChanged={props.onChanged} />
                  )}
                </div>
              ),
            },
          ]}
        />
        {error ? (
          <div role="alert" className="ledger-error">
            {error}
            <Button onClick={refresh}>重试</Button>
          </div>
        ) : loading ? (
          <div className="ledger-loading">
            <Spin />
          </div>
        ) : !selected ? (
          <Empty description="选择一个记忆库查看" />
        ) : !items.length ? (
          <Empty description="还没有记忆" />
        ) : (
          <div className="memory-list">
            {items.map((m) => (
              <article
                key={m.id}
                className={
                  "memory-card " + (m.id === props.highlight ? "highlight" : "")
                }
              >
                <div className="memory-status">
                  <Tag color={m.status === "active" ? "green" : undefined}>
                    {statusNames[m.status]}
                  </Tag>
                  <span>{m.kind === "goal" ? "目标" : m.kind === "fact" ? "个人信息" : "偏好"}</span>
                </div>
                <p className="memory-content">{m.content}</p>
                <div className="memory-meta">
                  {m.confidence} · {date(m.created)}
                </div>
                <Collapse
                  ghost
                  size="small"
                  defaultActiveKey={m.id === props.highlight ? ["source"] : []}
                  items={[
                    {
                      key: "source",
                      label: "来源原文",
                      children: (
                        <div className="memory-source">
                          <blockquote>{m.sourceMessage.text}</blockquote>
                          <time>{date(m.sourceMessage.created)}</time>
                          <div>有效期至 {date(m.expires)}</div>
                        </div>
                      ),
                    },
                  ]}
                />
                <div className="memory-actions">
                  <Button
                    size="small"
                    disabled={busy || m.status !== "active"}
                    onClick={() => {
                      setEdit(m);
                      setContent(m.content);
                    }}
                  >
                    修改
                  </Button>
                  <Button
                    size="small"
                    disabled={busy || m.status === "rejected"}
                    onClick={() => act(m, "reject")}
                  >
                    记错了
                  </Button>
                  <Button
                    size="small"
                    disabled={busy || m.status !== "active"}
                    onClick={() => act(m, "change_goal")}
                  >
                    我改主意了
                  </Button>
                  <Button size="small" type="text" danger disabled={busy || !props.canErase}
                    onClick={() => setErasing(m)}>删除</Button>
                </div>
                <label className="followup-toggle">
                  <span>在首页提醒我</span>
                  <Switch size="small" aria-label={`在首页提醒我：${m.content}`} checked={Boolean(m.followup_allowed)}
                    disabled={busy || (!m.followup_allowed && m.status !== "active")}
                    onChange={(allowed) => void toggleFollowup(m, allowed)} />
                </label>
              </article>
            ))}
          </div>
        )}
      </Drawer>
      {props.open && erasing && <EraseMemoryDialog key={erasing.id} memory={erasing}
        onClose={() => setErasing(undefined)} onChanged={(data) => {
          setItems((previous) => previous.filter((item) => !data.memories.includes(item.id)));
          props.onErased(data);
        }} />}
      <Modal
        title="修改记忆"
        open={!!edit}
        onCancel={() => setEdit(null)}
        okText="保存修改"
        cancelText="取消"
        confirmLoading={busy}
        okButtonProps={{ disabled: !content.trim() }}
        onOk={() => edit && act(edit, "edit", content)}
      >
        <Input.TextArea
          aria-label="修改记忆内容"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          maxLength={400}
          rows={4}
        />
      </Modal>
    </>
  );
}
