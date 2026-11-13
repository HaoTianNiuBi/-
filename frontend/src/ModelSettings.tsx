import { useEffect, useState } from "react";
import { Button, Collapse, Input, Space, Tag } from "antd";
import { readModelStatus, testModelConnection, saveModelKey } from "./api";
import type { ModelStatus } from "./api";

export default function ModelSettings({ open, onStatus, apiKey, onApiKeyChange: setApiKey }: {
  open: boolean; onStatus: (status: ModelStatus) => void;
  apiKey: string; onApiKeyChange: (value: string) => void;
}) {
  const [status, setStatus] = useState<ModelStatus>();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!open) setResult("");
  }, [open]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    readModelStatus().then((s) => {
      if (alive) { setStatus(s); onStatus(s); }
    }).catch(() => { if (alive) { setError(true); setResult("无法读取模型配置，请检查本地服务"); } });
    return () => { alive = false; };
  }, [open, onStatus]);
  async function refresh(test: boolean, search = false) {
    setBusy(true); setResult(""); setError(false);
    try {
      const s = await readModelStatus();
      setStatus(s); onStatus(s);
      if (test) {
        await testModelConnection(search);
        setResult(search ? "DeepSeek 网页搜索成功" : "DeepSeek 连接成功");
      } else setResult("配置已刷新");
    } catch (e) {
      setError(true);
      setResult(e instanceof Error ? e.message : "连接失败，请重试");
    } finally { setBusy(false); }
  }
  async function saveKey() {
    const value = apiKey.trim();
    if (busy || !value) return;
    setBusy(true); setResult(""); setError(false);
    try {
      const s = await saveModelKey(value);
      setStatus(s); onStatus(s); setApiKey("");
      setResult("DeepSeek Key 已保存");
    } catch (e) {
      setError(true);
      setResult(e instanceof Error ? e.message : "保存失败，请重试");
    } finally { setBusy(false); }
  }
  return <section className="model-settings" aria-label="模型与联网设置">
    <div className="model-settings-heading"><strong>DeepSeek</strong>
      <Tag color={status?.configured ? "green" : undefined}>
        {status?.configured ? "Key 已配置" : "待配置 Key"}
      </Tag>
    </div>
    <div className="model-name">{status?.model || "deepseek-flash"}</div>
    {status?.keyEditable === false ? <p className="model-name">API Key 由部署端配置</p> : <>
    <label className="input-label" htmlFor="deepseek-api-key">API Key</label>
    <div className="model-key-input">
      <Input.Password id="deepseek-api-key" value={apiKey}
        autoComplete="off" spellCheck={false} disabled={busy} maxLength={512}
        placeholder={status?.configured ? "输入新 Key 以替换" : "输入 DeepSeek API Key"}
        onChange={(e) => setApiKey(e.target.value)} onPressEnter={() => void saveKey()} />
      <Button type="primary" disabled={busy || !apiKey.trim()} onClick={() => void saveKey()}>保存 Key</Button>
    </div>
    </>}
    <Space wrap>
      <Button loading={busy} disabled={!!apiKey.trim()} onClick={() => refresh(true)}>测试连接</Button>
      <Button disabled={busy || !status?.configured || !!apiKey.trim()} onClick={() => void refresh(true, true)}>测试联网</Button>
      <Button type="text" disabled={busy} onClick={() => refresh(false)}>刷新配置</Button>
    </Space>
    {result && <p className={error ? "model-result error" : "model-result"} role="status">{result}</p>}
    <Collapse ghost size="small" items={[{key: "help", label: "配置方法", children: <>
      {status?.keyEditable !== false && <p>输入 Key → 保存 Key → 测试连接，无需重启。Key 保存在本机后端配置。</p>}
      <p>对话会将当前段内容与获准记忆发送给 DeepSeek；连接测试只发送“请只回复 OK”。</p>
    </>}]} />
  </section>;
}
