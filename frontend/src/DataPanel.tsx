import { useState } from "react";
import { App, Button, Checkbox, Modal, Radio, Select, Space } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { eraseMemory, eraseWorkspace, exportLibrary, readWorkspaceDataState } from "./api";
import type { Memory } from "./api";
import { downloadLibrary } from "./data-export";
import { applyWorkspaceData } from "./store";
import type { State, WorkspaceData } from "./store";
import "./planning.css";

type ChangeHandler = (data: WorkspaceData) => void;

export default function DataPanel({ state, initialLibrary, disabled, onClose, onChanged }: {
  state: State;
  initialLibrary: string | null;
  disabled: boolean;
  onClose: () => void;
  onChanged: ChangeHandler;
}) {
  const [library, setLibrary] = useState(initialLibrary ?? "");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"json" | "md" | "reset">();
  const { message } = App.useApp();

  async function download(format: "json" | "md") {
    setBusy(format);
    try {
      const snapshot = await exportLibrary(library || null, state.libraries);
      const changes = await readWorkspaceDataState();
      onChanged(changes);
      if (snapshot.workspace !== changes.workspace || snapshot.revision !== changes.revision) {
        throw new Error("导出期间数据已变化，请重试");
      }
      downloadLibrary(snapshot, applyWorkspaceData(state, changes), format);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "导出失败，请重试");
    } finally {
      setBusy(undefined);
    }
  }

  async function clear() {
    setBusy("reset");
    try {
      onChanged(await eraseWorkspace());
      onClose();
      message.success("已清空这个空间");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "清空失败，请重试");
    } finally {
      setBusy(undefined);
    }
  }

  return <Modal open centered width={520} rootClassName="planning-modal"
    title={confirming ? "清空这个空间？" : "数据管理"} onCancel={confirming ? () => setConfirming(false) : onClose}
    closable={!busy} maskClosable={!busy} keyboard={!busy}
    footer={confirming ? <Space>
      <Button onClick={() => setConfirming(false)} disabled={Boolean(busy)}>取消</Button>
      <Button danger type="primary" loading={busy === "reset"} onClick={() => void clear()}>清空全部数据</Button>
    </Space> : null}>
    {confirming ? <p className="modal-copy">将永久删除这个空间的全部对话、记忆库、档案、提醒和胶囊，敏感记忆设置也会重置。</p> :
      <div className="planning-list">
        <label className="planning-library">导出范围
          <Select aria-label="导出范围" value={library} onChange={setLibrary} disabled={Boolean(busy)}
            options={[...state.libraries.map((item) => ({ value: item.id, label: item.name })),
              { value: "", label: "未绑定对话" }]} />
        </label>
        <Space wrap>
          <Button icon={<DownloadOutlined />} loading={busy === "json"} disabled={Boolean(busy)}
            onClick={() => void download("json")}>导出 JSON</Button>
          <Button icon={<DownloadOutlined />} loading={busy === "md"} disabled={Boolean(busy)}
            onClick={() => void download("md")}>导出 Markdown</Button>
        </Space>
        <Button type="text" danger disabled={disabled || Boolean(busy)}
          onClick={() => setConfirming(true)}>清空全部数据</Button>
      </div>}
  </Modal>;
}

export function EraseMemoryDialog({ memory, onClose, onChanged }: {
  memory: Memory;
  onClose: () => void;
  onChanged: ChangeHandler;
}) {
  const [scope, setScope] = useState<"memory" | "source">("memory");
  const [blockSource, setBlockSource] = useState(true);
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();

  async function erase() {
    setBusy(true);
    try {
      onChanged(await eraseMemory(memory.id, memory.version, scope, blockSource));
      onClose();
      message.success("已删除");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "删除失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return <Modal open centered width={500} rootClassName="planning-modal" title="永久删除记忆？"
    onCancel={onClose} onOk={() => void erase()} okText="永久删除" cancelText="取消"
    confirmLoading={busy} closable={!busy} maskClosable={!busy} keyboard={!busy}
    okButtonProps={{ danger: true }} cancelButtonProps={{ disabled: busy }}>
    <div className="planning-list">
      <blockquote>{memory.content}</blockquote>
      <Radio.Group value={scope} onChange={(event) => setScope(event.target.value)} disabled={busy}>
        <Space direction="vertical">
          <Radio value="memory">仅删除这条记忆</Radio>
          <Radio value="source">同时删除来源消息</Radio>
        </Space>
      </Radio.Group>
      {scope === "memory" ? <Checkbox checked={blockSource} disabled={busy}
        onChange={(event) => setBlockSource(event.target.checked)}>禁止从这条原文再次提取记忆</Checkbox> :
        <p className="planning-muted">原文、同源记忆、相关打卡、提醒和决定会一并清理。引用过这些内容的回复也可能被清除。</p>}
      {scope === "memory" && <p className="planning-muted">关联的画像、提醒和回复会同步清理。</p>}
    </div>
  </Modal>;
}
