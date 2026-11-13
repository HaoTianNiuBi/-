import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  App as AntApp,
  Button,
  Drawer,
  Input,
  Modal,
  Select,
  Tooltip,
} from "antd";
import {
  ArrowUpOutlined,
  PlusOutlined,
  HomeOutlined,
  MessageOutlined,
  FolderOutlined,
  SettingOutlined,
  ArrowRightOutlined,
  MenuOutlined,
  EditOutlined,
  SafetyOutlined,
  CheckOutlined,
  DeleteOutlined,
  UndoOutlined,
  BookOutlined,
  GlobalOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { applyWorkspaceData, load, readWorkspaceData, rememberWorkspaceData, save, uid, modes, workspaceDataKey } from "./store";
import type { Conversation, Mode, WorkspaceData } from "./store";
import LivingScene from "./LivingScene";
import MemoryLedger from "./MemoryLedger";
import ModelSettings from "./ModelSettings";
import DataPanel from "./DataPanel";
import PlanningPanel, { PlanningCards } from "./PlanningPanel";
import type { PlanningView } from "./PlanningPanel";
import type { ArchiveTab } from "./ArchivePanel";
import { ChallengePrompt, ReminderCards } from "./MemoryWorkflows";
import { actOnReminder, connect, eraseConversation, readWorkspaceDataState, sendTurn, extractionLabels, setConversationDeleted,
  readModelStatus, readPendingChallenge, leaveChallengeScope, resolveChallenge, useWorkspace } from "./api";
import type { TurnRequest, ModelStatus, Challenge, ChallengeDecision, Memory, ReminderAction } from "./api";

const ArchivePanel = lazy(() => import("./ArchivePanel"));

function Mark({ small = false }: { small?: boolean }) {
  return (
    <img
      className={small ? "mark small" : "mark"}
      src="/assets/qiyu-logo.png"
      alt=""
    />
  );
}

export default function App() {
  const [state, setState] = useState(load);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [scope, setScope] = useState<string | null>("life");
  const [mode, setMode] = useState<Mode>("分析");
  const [text, setText] = useState("");
  const [webSearch, setWebSearch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [replyPreview, setReplyPreview] = useState<{ conversation: string; text: string }>();
  const followReply = useRef(true);
  const [scenePaused, setScenePaused] = useState(false);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [archivePanel, setArchivePanel] = useState<{ library: string | null; tab: ArchiveTab }>();
  const [planningView, setPlanningView] = useState<PlanningView>();
  const archiveTrigger = useRef<HTMLElement>(null);
  const archiveOpen = Boolean(archivePanel);
  const [ledgerLibrary, setLedgerLibrary] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string>();
  const [memoryRevision, setMemoryRevision] = useState(0);
  const [pendingChallenge, setPendingChallenge] = useState<Challenge | null>(null);
  const refreshMemory = useCallback(() => setMemoryRevision((v) => v + 1), []);
  const [serviceReady, setServiceReady] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelStatus>();
  const onModelStatus = useCallback((value: ModelStatus) => {
    setModelStatus(value);
    setServiceReady(true);
  }, []);
  const [mobile, setMobile] = useState(false);
  const [libraryModal, setLibraryModal] = useState(false);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [libraryName, setLibraryName] = useState("");
  const [settings, setSettings] = useState(false);
  const [dataPanel, setDataPanel] = useState(false);
  const [purgingConversation, setPurgingConversation] = useState<Conversation>();
  const workspaceData = useRef<WorkspaceData>(undefined);
  const knownReset = useRef({ lineage: state.workspaceLineage, count: state.workspaceReset ?? 0 });
  const [modelApiKey, setModelApiKey] = useState("");
  useEffect(() => {
    if (!settings) setModelApiKey("");
  }, [settings]);
  const [allChats, setAllChats] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const [changingConversation, setChangingConversation] = useState(false);
  const [displayName, setDisplayName] = useState(state.name);
  const bottom = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  const { message } = AntApp.useApp();
  const chat = state.conversations.find((c) => c.id === activeId);
  const typingText = replyPreview?.conversation === activeId ? replyPreview.text : "";
  const currentScope = chat ? chat.library : scope;
  const currentMode = chat?.mode || mode;
  const segment = chat?.entries.filter((entry) => entry.role === "divider").at(-1)?.id || chat?.id;
  const challenge = pendingChallenge?.segment === segment && pendingChallenge?.library === currentScope
    && (currentMode === "挑战" || currentMode === "自动") ? pendingChallenge : null;
  const libraryLabel = (id: string | null) =>
    state.libraries.find((l) => l.id === id)?.name || "不使用长期记忆";
  const applyDataChanges = useCallback((incoming: WorkspaceData) => {
    const previous = workspaceData.current;
    if (previous && previous.lineage === incoming.lineage && (previous.reset > incoming.reset
        || (previous.workspace === incoming.workspace && previous.revision >= incoming.revision))) return;
    const data = rememberWorkspaceData(incoming);
    workspaceData.current = data;
    useWorkspace(data.workspace);
    setState((state) => applyWorkspaceData(state, data));
    setReplyPreview(undefined);
    setPendingChallenge(null);
    const known = knownReset.current;
    knownReset.current = { lineage: data.lineage, count: data.reset };
    if ((!known.lineage || known.lineage === data.lineage) && data.reset > known.count) {
      setActiveId(null);
      setScope("life");
      setMode("分析");
      setDisplayName("体验者");
      setText("");
      setWebSearch(false);
      setLedgerOpen(false);
      setAllChats(false);
      setShowDeleted(false);
      setPlanningView(undefined);
      setArchivePanel(undefined);
      if (location.hash === "#archive") history.replaceState(null, "", location.pathname + location.search);
    }
    refreshMemory();
  }, [refreshMemory]);
  useEffect(() => {
    const restoreArchive = () => {
      if (location.hash === "#archive") {
        const previous = history.state?.qiyuArchive as typeof archivePanel;
        setArchivePanel(previous || { library: currentScope, tab: "profile" });
      } else {
        setArchivePanel(undefined);
        requestAnimationFrame(() => archiveTrigger.current?.focus({ preventScroll: true }));
      }
    };
    if (location.hash === "#archive") restoreArchive();
    window.addEventListener("popstate", restoreArchive);
    return () => window.removeEventListener("popstate", restoreArchive);
  }, [currentScope]);
  useEffect(() => {
    if (!archiveOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [archiveOpen]);
  useEffect(() => {
    try {
      save(state);
    } catch {
      message.error("浏览器存储不可用或已满，本次修改可能无法保存。");
    }
  }, [state, message]);
  useEffect(() => {
    connect()
      .then(async () => {
        applyDataChanges(await readWorkspaceDataState());
        setServiceReady(true);
        setModelStatus(await readModelStatus());
      })
      .catch(() => setServiceReady(false));
  }, [applyDataChanges]);
  useEffect(() => {
    const sync = () => { void readWorkspaceDataState().then(applyDataChanges).catch(() => {}); };
    const storage = (event: StorageEvent) => {
      if (event.key !== workspaceDataKey) return;
      const data = readWorkspaceData();
      if (data) applyDataChanges(data);
    };
    window.addEventListener("focus", sync);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener("focus", sync);
      window.removeEventListener("storage", storage);
    };
  }, [applyDataChanges]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "instant" });
  }, [chat?.entries.length, busy]);
  useEffect(() => {
    if (typingText && followReply.current) bottom.current?.scrollIntoView({ behavior: "instant" });
  }, [typingText]);
  useEffect(() => {
    if (chat?.entries.length === 0) field.current?.focus();
    setWebSearch(false);
  }, [activeId]);
  useEffect(() => {
    if (!activeId || !segment || !currentScope) return;
    let live = true;
    readPendingChallenge(activeId, segment).then((result) => {
      if (live) setPendingChallenge(result);
    }).catch(() => {
      if (live) setPendingChallenge(null);
    });
    return () => { live = false; };
  }, [activeId, segment, currentScope, memoryRevision]);
  function updateChat(id: string, fn: (c: Conversation) => Conversation) {
    setState((s) => applyWorkspaceData({
      ...s,
      conversations: s.conversations.map((c) => (c.id === id ? fn(c) : c)),
    }, workspaceData.current));
  }
  function goHome() {
    setActiveId(null);
    setText("");
    setMobile(false);
    setAllChats(false);
  }
  function newChat() {
    if (busy) return;
    const empty = state.conversations.find(
      (c) =>
        c.entries.length === 0 &&
        c.library === currentScope &&
        c.mode === currentMode,
    );
    const id = empty?.id || uid();
    if (!empty) {
      const conversation: Conversation = {
        id,
        title: "新对话",
        library: currentScope,
        mode: currentMode,
        entries: [],
        updated: Date.now(),
      };
      setState((s) => ({
        ...s,
        conversations: [conversation, ...s.conversations],
      }));
    }
    setActiveId(id);
    setText("");
    setMobile(false);
    setAllChats(false);
    field.current?.focus();
  }
  function selectChat(id: string) {
    setActiveId(id);
    setText("");
    setMobile(false);
    setAllChats(false);
  }
  async function changeScope(value: string, labelOverride?: string) {
    const id = value === "none" ? null : value;
    if (chat && chat.library !== id && segment && chat.entries.some((entry) => entry.role === "user")) {
      setBusy(true);
      try {
        await leaveChallengeScope(chat.id, segment);
      } catch (error) {
        message.error(error instanceof Error ? error.message : "无法切换记忆库");
        return;
      } finally {
        setBusy(false);
      }
    }
    setScope(id);
    if (chat && chat.library !== id) {
      updateChat(chat.id, (c) => ({
        ...c,
        library: id,
        entries: [
          ...c.entries,
          {
            id: uid(),
            role: "divider",
            text: `已切换到${id ? "「" + (labelOverride || libraryLabel(id)) + "」" : "不使用长期记忆"}，从这里开始新上下文`,
            library: id,
          },
        ],
      }));
      message.info("已开始新上下文段，旧消息保留供你翻看。");
    }
  }
  function changeMode(value: Mode) {
    setMode(value);
    if (chat) updateChat(chat.id, (c) => ({ ...c, mode: value }));
  }
  function toggleWebSearch() {
    if (!modelStatus?.configured && !webSearch) {
      setSettings(true);
      return;
    }
    setWebSearch((enabled) => !enabled);
  }
  async function send(value = text, search = false) {
    const content = value.trim();
    if (!content || busy) return;
    if (search && Array.from(content.replace(/\s+/g, " ")).length > 128) {
      message.warning("联网查询最多支持 128 个字符，请缩短问题后再发送");
      return;
    }
    const id = chat?.id || uid();
    const selected = currentScope;
    const selectedMode = currentMode;
    const entry = {
      id: uid(),
      role: "user" as const,
      text: content,
      library: selected,
      extraction: "processing",
      timestamp: Date.now(),
    };
    if (!chat) {
      const c: Conversation = {
        id,
        title: content.slice(0, 24),
        library: selected,
        mode: selectedMode,
        entries: [entry],
        updated: Date.now(),
      };
      setState((s) => ({ ...s, conversations: [c, ...s.conversations] }));
      setActiveId(id);
    } else
      updateChat(id, (c) => ({
        ...c,
        title: c.entries.some((e) => e.role === "user")
          ? c.title
          : content.slice(0, 24),
        entries: [...c.entries, entry],
        updated: Date.now(),
      }));
    setText("");
    setWebSearch(false);
    setBusy(true);
    const divider = chat?.entries.filter((e) => e.role === "divider").at(-1);
    const request: TurnRequest = {
      id: entry.id,
      conversation: id,
      segment: divider?.id || id,
      library: selected,
      text: content,
      mode: selectedMode,
      webSearch: search || undefined,
    };
    updateChat(id, (c) => ({
      ...c,
      entries: c.entries.map((e) =>
        e.id === entry.id ? { ...e, request } : e,
      ),
    }));
    await runTurn(request);
  }
  function createTyping(conversation: string) {
    const dataAtStart = workspaceData.current;
    let pending: string[] = [];
    let shown = "";
    let drained: (() => void) | undefined;
    const timer = window.setInterval(() => {
      const character = pending.shift();
      if (character !== undefined) {
        const end = bottom.current;
        followReply.current = !end || end.getBoundingClientRect().bottom <= window.innerHeight + 120;
        shown += character;
        if (workspaceData.current === dataAtStart) setReplyPreview({ conversation, text: shown });
      }
      if (!pending.length && drained) {
        drained();
        drained = undefined;
      }
    }, 16);
    return {
      receive: (delta: string) => { pending.push(...delta); },
      async finish(text: string) {
        pending = text.startsWith(shown) ? Array.from(text.slice(shown.length)) : [];
        if (pending.length) await new Promise<void>((resolve) => { drained = resolve; });
      },
      dispose() {
        window.clearInterval(timer);
        setReplyPreview(undefined);
      },
    };
  }
  async function runTurn(request: TurnRequest) {
    setBusy(true);
    const typing = createTyping(request.conversation);
    updateChat(request.conversation, (c) => ({
      ...c,
      entries: c.entries.map((e) =>
        e.id === request.id
          ? { ...e, extraction: "processing", error: undefined }
          : e,
      ),
    }));
    try {
      const result = await sendTurn(request, state.libraries, typing.receive);
      const isReminder = result.provider === "local-reminder";
      if (!isReminder && result.provider !== "local-confirmation") await typing.finish(result.text);
      setServiceReady(true);
      if (result.provider === "deepseek") setModelStatus({
        provider: "deepseek", configured: true, model: result.model || "deepseek-flash",
      });
      updateChat(request.conversation, (c) => c.entries.some((entry) => entry.id === request.id) ? ({
        ...c,
        entries: [
          ...c.entries.map((e) =>
            e.id === request.id
              ? { ...e, extraction: result.extraction, memoryCount: result.created.length, archive: result.archive,
                  decisionDraftId: result.decisionDraftId, error: undefined }
              : e,
          ),
          ...(!c.entries.some((e) => e.id === "reply-" + request.id)
            ? [
                {
                  id: "reply-" + request.id,
                  role: isReminder ? "notice" as const : "assistant" as const,
                  text: result.text,
                  behavior: result.behavior,
                  provider: result.provider,
                  model: result.model,
                  library: request.library,
                  citations: result.citations,
                  webSources: result.webSources,
                  webSourcesCited: result.webSourcesCited,
                  capsuleRefs: result.capsuleRefs,
                  timestamp: result.timestamp,
                  reminderId: result.reminderId,
                },
              ]
            : []),
        ],
      }) : c);
      refreshMemory();
    } catch (e) {
      const error = e instanceof Error ? e.message : "本地服务暂不可用";
      updateChat(request.conversation, (c) => ({
        ...c,
        entries: c.entries.map((m) =>
          m.id === request.id ? { ...m, extraction: "failed", error } : m,
        ),
      }));
      message.error(error);
    } finally {
      typing.dispose();
      setBusy(false);
    }
  }
  async function decideChallenge(decision: ChallengeDecision, checks: boolean[]) {
    if (!challenge || !chat || busy) return;
    setBusy(true);
    const typing = createTyping(chat.id);
    try {
      const result = await resolveChallenge(challenge, decision, checks);
      setPendingChallenge(null);
      if (result.text) {
        await typing.finish(result.text);
        const reply = result.text;
        updateChat(chat.id, (c) => c.entries.some((entry) => entry.id === result.id) ? c : ({
          ...c,
          entries: [...c.entries, {
            id: result.id, role: "assistant", text: reply, library: challenge.library,
            behavior: "挑战", challengeConfirmed: true, provider: "deepseek", model: result.model,
            citations: result.citations, timestamp: result.timestamp,
          }],
        }));
      } else {
        const summary = decision === "goal_changed" && challenge.memory.kind === "goal"
          ? "旧目标已失效，后续对话会跳过它。"
          : decision === "skip" ? "本次核对已跳过。" : "本次核对已结束。";
        updateChat(chat.id, (c) => c.entries.some((entry) => entry.id === result.id) ? c : ({
          ...c,
          entries: [...c.entries, { id: result.id, role: "notice", text: summary,
            library: challenge.library, timestamp: result.timestamp }],
        }));
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "核对失败，请重试");
    } finally {
      typing.dispose();
      refreshMemory();
      setBusy(false);
    }
  }
  function startFollowup(memory: Memory) {
    setScope(memory.bid);
    setText(`我想继续聊聊这条记录：${memory.content}`);
    field.current?.focus();
  }
  async function updateReminder(id: string, action: ReminderAction) {
    const { status } = await actOnReminder(id, action);
    if (status !== "pending") {
      setState((previous) => ({
        ...previous,
        conversations: previous.conversations.map((conversation) =>
          conversation.entries.some((entry) => entry.reminderId === id)
            ? { ...conversation, entries: conversation.entries.map((entry) =>
                entry.reminderId === id ? { ...entry, reminderStatus: status } : entry) }
            : conversation,
        ),
      }));
    }
    refreshMemory();
  }
  async function cancelReminder(id: string) {
    try {
      await updateReminder(id, "cancel");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消提醒失败");
    }
  }
  function openLedger(library = currentScope, memoryId?: string) {
    setLedgerLibrary(library);
    setHighlight(memoryId);
    setLedgerOpen(true);
  }
  function openArchive(library = currentScope, tab: ArchiveTab = "profile") {
    setMobile(false);
    archiveTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const page = { library, tab };
    if (location.hash !== "#archive") history.pushState({ qiyuArchive: page }, "", "#archive");
    setArchivePanel(page);
  }
  function closeArchive() {
    if (history.state?.qiyuArchive) {
      history.back();
    } else {
      history.replaceState(null, "", location.pathname + location.search);
      setArchivePanel(undefined);
    }
    requestAnimationFrame(() => archiveTrigger.current?.focus({ preventScroll: true }));
  }

  function saveLibrary() {
    const name = libraryName.trim();
    if (!name) return;
    if (state.libraries.some((l) => l.name === name && l.id !== renameId)) {
      message.warning("已经有同名记忆库，换一个名称吧。");
      return;
    }
    const id = renameId || uid();
    setState((s) => ({
      ...s,
      libraries: renameId
        ? s.libraries.map((l) => (l.id === id ? { ...l, name } : l))
        : [...s.libraries, { id, name }],
    }));
    setLibraryModal(false);
    if (!renameId) changeScope(id, name);
    message.success(renameId ? "名称已更新" : "记忆库已创建，当前为空");
  }
  function editLibrary(id: string | null) {
    setRenameId(id);
    setLibraryName(state.libraries.find((l) => l.id === id)?.name || "");
    setLibraryModal(true);
  }
  async function moveConversation(c: Conversation, deleted: boolean) {
    if (busy || changingConversation) return;
    setChangingConversation(true);
    try {
      await setConversationDeleted(c.id, deleted);
      setState((s) => ({
        ...s,
        conversations: deleted ? s.conversations.filter((v) => v.id !== c.id)
          : [c, ...s.conversations.filter((v) => v.id !== c.id)],
        deletedConversations: deleted ? [c, ...s.deletedConversations.filter((v) => v.id !== c.id)]
          : s.deletedConversations.filter((v) => v.id !== c.id),
      }));
      if (deleted && activeId === c.id) {
        setActiveId(null);
        setText("");
      }
      setMemoryRevision((v) => v + 1);
      message.success(deleted ? "已移至最近删除" : "对话已恢复");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      setChangingConversation(false);
    }
  }
  async function purgeConversation() {
    if (!purgingConversation || busy || changingConversation) return;
    setChangingConversation(true);
    try {
      applyDataChanges(await eraseConversation(purgingConversation.id));
      setPurgingConversation(undefined);
      message.success("对话已永久删除");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "删除失败，请重试");
    } finally {
      setChangingConversation(false);
    }
  }
  const conversationList = (limit?: number, deleted = false) =>
    (deleted ? state.deletedConversations : state.conversations).slice(0, limit).map((c) => (
      <div className="conversation-row" key={c.id}>
      <button
        className={"conversation " + (activeId === c.id ? "selected" : "")}
        onClick={() => deleted ? moveConversation(c, false) : selectChat(c.id)}
        disabled={deleted && (busy || changingConversation)}
      >
        <MessageOutlined />
        <span>
          {c.title}
          <small>{libraryLabel(c.library)}</small>
        </span>
      </button>
      <Tooltip title={deleted ? "恢复对话" : "删除对话"}>
        <button className="conversation-action"
          aria-label={(deleted ? "恢复对话：" : "删除对话：") + c.title}
          disabled={busy || changingConversation}
          onClick={() => moveConversation(c, !deleted)}>
          {deleted ? <UndoOutlined /> : <DeleteOutlined />}
        </button>
      </Tooltip>
      {deleted && <Tooltip title="永久删除对话">
        <button className="conversation-action" aria-label={"永久删除对话：" + c.title}
          disabled={busy || changingConversation} onClick={() => setPurgingConversation(c)}>
          <DeleteOutlined />
        </button>
      </Tooltip>}
      </div>
    ));
  const sidebar = (
    <>
      <div className="brand">
        <Mark />
        <div>
          栖语<span>留一处空间，给自己</span>
        </div>
      </div>
      <button className="new-chat" onClick={newChat} disabled={busy}>
        <PlusOutlined /> 开始新的对话 <span>＋</span>
      </button>
      <nav aria-label="主导航">
        <button
          className={!chat ? "nav-item active" : "nav-item"}
          onClick={goHome}
          disabled={busy}
        >
          <HomeOutlined />
          我的空间
        </button>
        <button
          className="nav-item"
          onClick={() => {
            setShowDeleted(false);
            setAllChats(true);
            setMobile(false);
          }}
          disabled={busy}
        >
          <MessageOutlined />
          全部对话<span className="count">{state.conversations.length}</span>
        </button>
        <div className="sidebar-tools" role="group" aria-label="空间工具" tabIndex={0}>
          <button className="nav-item memory-ledger-nav" onClick={() => openLedger()}>
            <FolderOutlined />
            记忆台账
          </button>
          <button className="nav-item" onClick={() => openArchive()} disabled={busy}>
            <BookOutlined />
            档案
          </button>
          <button className="nav-item" disabled={busy} onClick={() => { setMobile(false); setPlanningView({ kind: "capsules", library: currentScope }); }}>
            <ClockCircleOutlined />
            时间胶囊
          </button>
        </div>
      </nav>
      <div className="side-label">
        记忆空间
        <Tooltip title="新建记忆库">
          <button
            aria-label="新建记忆库"
            onClick={() => editLibrary(null)}
            disabled={busy}
          >
            <PlusOutlined />
          </button>
        </Tooltip>
      </div>
      <div className="libraries">
        {state.libraries.map((l) => (
          <div
            key={l.id}
            className={"library-row " + (currentScope === l.id ? "chosen" : "")}
          >
            <button
              disabled={busy}
              onClick={() => {
                changeScope(l.id);
                setMobile(false);
              }}
            >
              <span className="library-dot" />
              {l.name}
            </button>
            <Tooltip title="重命名">
              <button
                aria-label={"重命名" + l.name}
                onClick={() => editLibrary(l.id)}
              >
                <EditOutlined />
              </button>
            </Tooltip>
          </div>
        ))}
        <button
          className={"no-memory " + (currentScope === null ? "chosen" : "")}
          onClick={() => changeScope("none")}
          disabled={busy}
        >
          <SafetyOutlined />
          不使用长期记忆
        </button>
      </div>
      <div className="side-label recent-label">最近对话</div>
      <div className="recent" role="region" aria-label="最近对话" tabIndex={0}>
        {state.conversations.length ? (
          conversationList(8)
        ) : (
          <p className="empty-recent">
            新的故事，从这一次开始。
            <br />
            聊过的内容会留在这里。
          </p>
        )}
      </div>
      <div className="sidebar-bottom">
        <div className="version-note">
          <span className="status-dot" />
          本地体验版{" "}
          <span>
            v0.6
          </span>
        </div>
        <button
          className="profile"
          onClick={() => {
            setDisplayName(state.name);
            setSettings(true);
          }}
        >
          <span className="avatar">{state.name.slice(0, 1)}</span>
          <span>
            {state.name}
            <small>此浏览器的私人空间</small>
          </span>
          <SettingOutlined />
        </button>
      </div>
    </>
  );
  return (
    <div className={archiveOpen ? "app-shell has-archive" : "app-shell"}>
      {!archiveOpen && <a className="skip" href="#main">
        跳到主要内容
      </a>}
      <aside className="sidebar" inert={archiveOpen}>{sidebar}</aside>
      <Drawer
        placement="left"
        open={mobile}
        onClose={() => setMobile(false)}
        size={280}
        styles={{ body: { padding: 0 } }}
      >
        <div className="mobile-sidebar">{sidebar}</div>
      </Drawer>
      <div className={`workspace ${chat ? "scene-chat" : "scene-home"}${archiveOpen ? " scene-archive" : ""}${scenePaused ? " scene-motion-paused" : ""}`} inert={archiveOpen}>
        <LivingScene
          conversationId={chat?.id || null}
          scopeKey={(chat?.id || "home") + (currentScope || "none")}
          rounds={
            chat?.entries
              .slice(chat.entries.map((e) => e.role).lastIndexOf("divider") + 1)
              .filter((e) => e.role === "assistant").length || 0
          }
          paused={scenePaused}
          archive={archiveOpen}
        />
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu"
              aria-label="打开导航"
              onClick={() => setMobile(true)}
            >
              <MenuOutlined />
            </button>
            <span>我的空间</span>
            <span className="slash">/</span>
            <strong>{chat ? "对话" : "今日"}</strong>
          </div>
          <div className="scene-tools">
            <button
              className="scene-pause"
              aria-pressed={scenePaused}
              onClick={() => setScenePaused((p) => !p)}
            >
              {scenePaused ? "恢复背景动效" : "暂停背景动效"}
            </button>
            <button className="demo-badge" onClick={() => setSettings(true)}>
              <span />
              {serviceReady ? (modelStatus?.configured ? "DeepSeek · 已配置" : "DeepSeek · 待配置") : "服务未连接"}
            </button>
          </div>
        </header>
        <main id="main" className={chat ? "main chat-main" : "main home-main"}>
          {!chat ? (
            <>
              <section className="welcome">
                <div className="welcome-copy">
                  <div className="welcome-date">一段属于你的时间</div>
                  <h1>
                    有些话，
                    <br />
                    可以慢慢说<span>。</span>
                  </h1>
                  <p>
                    整理思绪，或者只是聊聊今天。
                    <br />
                    在这里，按照自己的节奏来。
                  </p>
                </div>
              </section>
              <ReminderCards library={currentScope} libraries={state.libraries} revision={memoryRevision}
                disabled={busy} onChanged={refreshMemory} onStart={startFollowup} onReminder={updateReminder} />
              <PlanningCards library={currentScope} libraries={state.libraries} revision={memoryRevision} disabled={busy}
                onOpen={setPlanningView} />
              <div className="conversation-intro">
                <span className="tiny-spark">✧</span>
                <span>今天，想从哪里说起？</span>
              </div>
            </>
          ) : (
            <section className="chat-heading">
              <div>
                <span className="eyebrow">这一次的对话</span>
                <h1>{chat.title}</h1>
              </div>
              <span className="local-saved">
                <CheckOutlined />
                保存在此浏览器
              </span>
            </section>
          )}
          {chat && (
            <div
              className="messages"
              aria-live="polite"
              aria-relevant="additions"
            >
              {chat.entries.filter((entry) => entry.provider !== "local-confirmation").map((e) =>
                e.role === "divider" || e.role === "notice" ? (
                  <div className="context-divider" key={e.id}>
                    <span>{e.reminderStatus === "cancelled" ? "提醒已取消" : e.reminderStatus === "done" ? "提醒已完成" : e.text}</span>
                    {e.reminderId && !e.reminderStatus && (
                      <button className="workflow-link reminder-cancel" onClick={() => void cancelReminder(e.reminderId!)}>取消提醒</button>
                    )}
                  </div>
                ) : (
                  <article key={e.id} className={"message " + e.role}>
                    <div className="message-avatar">
                      {e.role === "assistant" ? (
                        <Mark small />
                      ) : (
                        state.name.slice(0, 1)
                      )}
                    </div>
                    <div className="message-content">
                      <div className="message-meta">
                        {e.role === "assistant" ? "栖语" : state.name}
                        {e.role === "assistant" && (
                          <span>{e.provider === "deepseek" ? "DeepSeek" : "规则回复"} · {e.challengeConfirmed ? "挑战 · 已确认" : e.behavior || "分析"}</span>
                        )}
                      </div>
                      <div className="message-text">{e.text}</div>
                      {!!e.webSources?.length && (
                        <details className="web-sources">
                          <summary>{e.webSourcesCited ? "网页来源" : "搜索结果"} · {e.webSources.length}</summary>
                          <ul>
                            {e.webSources.filter((source) => /^https?:\/\//i.test(source.url)).map((source) => (
                              <li key={source.number}>
                                <a href={source.url} target="_blank" rel="noopener noreferrer">
                                  {source.number}. {source.title}
                                </a>
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                      {e.citations?.length ? (
                        <div className="memory-citations">
                          {e.citations.map((id, i) => (
                            <button
                              key={id}
                              className="memory-citation"
                              onClick={() => openLedger(e.library, id)}
                            >
                              来源 {i + 1}
                            </button>
                          ))}
                        </div>
                      ) : null}
                      {!!e.capsuleRefs?.length && <div className="memory-citations">
                        {e.capsuleRefs.map((item) => <button key={item.id} className="memory-citation"
                          onClick={() => setPlanningView({ kind: "capsules", library: e.library, id: item.id, version: item.version })}>
                          {item.title} · 第 {item.version} 版 · {new Date(item.saved).toLocaleDateString("zh-CN")}
                        </button>)}
                      </div>}
                      {e.role === "user" && e.decisionDraftId && <div className="extraction-state">
                        <button onClick={() => setPlanningView({ kind: "decisions", library: e.library, id: e.decisionDraftId })}>查看决定草稿</button>
                      </div>}
                      {e.role === "user" && e.extraction && !["reminder", "search_only"].includes(e.extraction) &&
                        !(e.extraction === "no_match" && e.archive && (e.archive.pending || e.archive.recorded || e.archive.changes)) && (
                        <div
                          className={
                            "extraction-state " + (e.error ? "failed" : "")
                          }
                          role="status"
                        >
                          <span>
                            {e.extraction === "recorded" && e.memoryCount
                              ? `已记入 ${e.memoryCount} 条`
                              : extractionLabels[e.extraction] || e.extraction}
                          </span>
                          {e.extraction === "recorded" && (
                            <button onClick={() => openLedger(e.library)}>
                              查看
                            </button>
                          )}
                          {e.error && e.request && (
                            <button
                              disabled={busy}
                              onClick={() => e.request && runTurn(e.request)}
                            >
                              重试
                            </button>
                          )}
                        </div>
                      )}
                      {e.role === "user" && e.archive && (
                        <div className="extraction-state" role="status">
                          {e.archive.pending > 0
                            ? <button onClick={() => openArchive(e.library, "tracking")}>核对 {e.archive.pending} 条打卡</button>
                            : e.archive.recorded > 0 && <button onClick={() => openArchive(e.library, "tracking")}>已打卡 {e.archive.recorded} 条</button>}
                          {e.archive.changes > 0 && <button onClick={() => openArchive(e.library, "changes")}>查看认知变化</button>}
                          {e.archive.unavailable && <span>档案提取暂不可用</span>}
                        </div>
                      )}
                    </div>
                  </article>
                ),
              )}
              {typingText && (
                <article className="message assistant" aria-label="正在生成回复">
                  <div className="message-avatar"><Mark small /></div>
                  <div className="message-content">
                    <div className="message-meta">栖语 <span>DeepSeek</span></div>
                    <div className="message-text">{typingText}<span className="reply-caret" aria-hidden="true" /></div>
                  </div>
                </article>
              )}
              {busy && !typingText && (
                <div className="responding" role="status">
                  <Mark small />
                  {chat.entries.some((entry) => entry.extraction === "processing" && entry.request?.webSearch)
                    ? "正在联网搜索" : "正在处理"}<span className="loading-dots">…</span>
                </div>
              )}
              {challenge && <ChallengePrompt key={challenge.id} challenge={challenge} busy={busy}
                onSource={() => openLedger(challenge.library, challenge.memory.id)} onDecide={decideChallenge} />}
              <div ref={bottom} />
            </div>
          )}
          <section className="composer-section" aria-label="输入对话">
            <div className="composer">
              <label className="sr-only" htmlFor="chat-input">
                你想聊些什么
              </label>
              <textarea
                id="chat-input"
                ref={field}
                value={text}
                onChange={(e) => setText(e.target.value)}
                maxLength={4000}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void send(text, webSearch);
                  }
                }}
                placeholder={
                  chat ? "继续说，我在这里。" : "此刻，你在想些什么？"
                }
                rows={chat ? 2 : 3}
              />
              <div className="composer-toolbar">
                <div className="composer-options">
                  <Select
                    aria-label="当前记忆库"
                    classNames={{ popup: { root: "qiyu-select-menu--sage" } }}
                    value={currentScope || "none"}
                    disabled={busy}
                    onChange={(value) => changeScope(value)}
                    variant="borderless"
                    popupMatchSelectWidth={230}
                    options={[
                      ...state.libraries.map((l) => ({
                        value: l.id,
                        label: l.name,
                      })),
                      { value: "none", label: "不使用长期记忆" },
                    ]}
                    suffix={<FolderOutlined />}
                  />
                  <span className="toolbar-divider" />
                  <Select
                    aria-label="行为模式"
                    classNames={{ popup: { root: "qiyu-select-menu--sage" } }}
                    value={currentMode}
                    disabled={busy}
                    onChange={changeMode}
                    variant="borderless"
                    options={modes.map((m) => ({
                      label: m === "自动" ? "自动模式" : m + "模式",
                      value: m,
                    }))}
                  />
                </div>
                <div className="composer-actions">
                  <Tooltip title={modelStatus?.configured ? "联网搜索" : "配置 DeepSeek"}>
                    <button type="button" className="web-search-toggle" aria-label="联网搜索"
                      aria-pressed={webSearch} disabled={busy} onClick={toggleWebSearch}>
                      <GlobalOutlined /> <span className="web-search-label">联网</span>
                    </button>
                  </Tooltip>
                  <button className="send" aria-label="发送消息" onClick={() => void send(text, webSearch)}
                    disabled={busy || !text.trim()}>
                    <ArrowUpOutlined />
                  </button>
                </div>
              </div>
            </div>
            <div className="composer-footnote">
              <span>
                <SafetyOutlined />
                {currentScope
                  ? `当前范围：${libraryLabel(currentScope)}`
                  : "当前不使用长期记忆 · 聊天原文仍保留在本机"}
              </span>
              {webSearch ? <span>本条使用 DeepSeek 网页搜索</span> :
                <span className="keyboard-hint">Enter 发送，Shift + Enter 换行</span>}
            </div>
          </section>
          {!chat && (
            <section className="starting-points" aria-label="对话起点">
              <button
                disabled={busy}
                onClick={() => send("今天有点累，想找个地方说说。")}
              >
                <span className="prompt-number">01</span>
                <span>
                  说说今天<small>给此刻的心情一点空间</small>
                </span>
                <ArrowRightOutlined />
              </button>
              <button
                disabled={busy}
                onClick={() => send("有一个决定，我想慢慢梳理。")}
              >
                <span className="prompt-number">02</span>
                <span>
                  理一理思绪<small>从一个还没想清的问题开始</small>
                </span>
                <ArrowRightOutlined />
              </button>
              <button
                disabled={busy}
                onClick={() => {
                  setText("我想聊一件最近一直放在心上的事：");
                  field.current?.focus();
                }}
              >
                <span className="prompt-number">03</span>
                <span>
                  随意聊一聊<small>不必准备好一个明确的话题</small>
                </span>
                <ArrowRightOutlined />
              </button>
            </section>
          )}
        </main>
      </div>
      <MemoryLedger
        open={ledgerOpen}
        onClose={() => setLedgerOpen(false)}
        libraries={state.libraries}
        library={ledgerLibrary}
        highlight={highlight}
        revision={memoryRevision}
        canErase={!busy}
        onChanged={refreshMemory}
        onErased={applyDataChanges}
      />
      {archivePanel && <Suspense fallback={null}>
        <ArchivePanel initialLibrary={archivePanel.library} initialTab={archivePanel.tab}
          libraries={state.libraries} name={state.name} revision={memoryRevision} onChanged={refreshMemory}
          paused={scenePaused} onClose={closeArchive}
          onDecision={(library, id, create) => setPlanningView({ kind: "decisions", library, id, create })} />
      </Suspense>}
      {planningView && <PlanningPanel view={planningView} libraries={state.libraries} revision={memoryRevision}
        onChanged={refreshMemory} onClose={() => setPlanningView(undefined)}
        onDiscuss={(library, prompt) => { setPlanningView(undefined); goHome(); setScope(library); setText(prompt); requestAnimationFrame(() => field.current?.focus()); }} />}
      <Modal
        title={renameId ? "重命名记忆库" : "创建一个记忆库"}
        open={libraryModal}
        onCancel={() => setLibraryModal(false)}
        onOk={saveLibrary}
        okText={renameId ? "保存名称" : "创建记忆库"}
        cancelText="取消"
        okButtonProps={{ disabled: !libraryName.trim() }}
        destroyOnHidden
      >
        <p className="modal-copy">
          同库会话共享获准使用的记忆，不同库保持独立。
        </p>
        <label className="input-label" htmlFor="library-name">
          记忆库名称
        </label>
        <Input
          id="library-name"
          autoFocus
          maxLength={24}
          value={libraryName}
          onChange={(e) => setLibraryName(e.target.value)}
          onPressEnter={saveLibrary}
          placeholder="例如：工作、日常、学习"
        />
      </Modal>
      <Modal
        title="你的本地空间"
        open={settings}
        onCancel={() => setSettings(false)}
        footer={<div className="capsule-actions">
          <Button type="text" onClick={() => { setSettings(false); setDataPanel(true); }}>数据管理</Button>
          <Button
            type="primary"
            onClick={() => {
              if (!displayName.trim()) return;
              setState((s) => ({ ...s, name: displayName.trim() }));
              setSettings(false);
            }}
            disabled={!displayName.trim()}
          >
            保存称呼
          </Button>
        </div>}
      >
        <p className="modal-copy">
          当前空间绑定到这个浏览器。你可以在数据管理中导出记录。
        </p>
        <label className="input-label" htmlFor="display-name">
          希望怎么称呼你
        </label>
        <Input
          id="display-name"
          value={displayName}
          maxLength={20}
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <ModelSettings open={settings} onStatus={onModelStatus}
          apiKey={modelApiKey} onApiKeyChange={setModelApiKey} />
      </Modal>
      {dataPanel && <DataPanel state={state} initialLibrary={currentScope} disabled={busy || changingConversation}
        onChanged={applyDataChanges} onClose={() => setDataPanel(false)} />}
      <Drawer
        title={showDeleted ? "最近删除" : "全部对话"}
        extra={<Button type="text" onClick={() => setShowDeleted((v) => !v)}>
          {showDeleted ? "全部对话" : "最近删除"}
        </Button>}
        rootClassName="conversation-drawer"
        open={allChats}
        onClose={() => setAllChats(false)}
        size={420}
      >
        <div className="all-chats">
          {(showDeleted ? state.deletedConversations : state.conversations).length ? (
            conversationList(undefined, showDeleted)
          ) : showDeleted ? (
            <div className="empty-state">没有已删除的对话</div>
          ) : (
            <div className="empty-state">
              <MessageOutlined />
              <h2>还没有对话</h2>
              <p>从今天的一件小事开始吧。</p>
              <Button type="primary" onClick={newChat}>
                开始新的对话
              </Button>
            </div>
          )}
        </div>
      </Drawer>
      {purgingConversation && <Modal open centered width={460} rootClassName="planning-modal"
        title="永久删除对话？" onCancel={() => setPurgingConversation(undefined)} onOk={() => void purgeConversation()}
        okText="永久删除" cancelText="取消" confirmLoading={changingConversation}
        closable={!changingConversation} maskClosable={!changingConversation} keyboard={!changingConversation}
        okButtonProps={{ danger: true }} cancelButtonProps={{ disabled: changingConversation }}>
        <p className="modal-copy">「{purgingConversation.title}」的消息、同源记忆、打卡、提醒和决定会一并清理。引用过这些内容的回复也可能被清除。</p>
      </Modal>}
    </div>
  );
}
