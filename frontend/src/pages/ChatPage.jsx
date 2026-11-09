import React, { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { chatPost, chatStream, apiGet, downloadFile, getBitrixTaskConfig, createBitrixTask } from '../api.js';
import { Send, Plus, Paperclip, X, FileDown, FileText, Upload, Download, ClipboardList } from 'lucide-react';
import { exportMessageToDocx, exportChatToDocx, exportChatToPdf, buildChatDocxBlob, chatDocxFilename } from '../utils/exportUtils.js';
import CheckResponse from '../components/CheckResponse.jsx';
import BitrixTaskModal from '../components/BitrixTaskModal.jsx';

const ALLOWED_EXT = ['.pdf', '.docx', '.doc', '.txt', '.md', '.jpg', '.jpeg', '.png'];
const MAX_FILES = 5;

function escapeMarkdownLinkText(s) {
  // Имя шаблона приходит от бэкенда (`documents.original_filename`).
  // Хотя сейчас оно подконтрольно админу, экранируем все markdown-метасимволы,
  // которые могут «развалить» рендер ссылки или дать неожиданный inline-формат:
  // - [ ] \ — синтаксис самой ссылки
  // - ( ) — закрытие URL-части
  // - * _ ~ — bold/italic/strike
  // - ` — inline code
  // - | — table cell в GFM
  // - < > — autolink и HTML
  // - # — заголовок при начале строки
  return s.replace(/([\\`*_{}\[\]()#+\-.!|~<>])/g, '\\$1');
}

function renderTemplateMarkers(content, templatesByName) {
  return content.replace(/\[\[ШАБЛОН:\s*([^\]]+?)\s*\]\]/g, (match, raw) => {
    const name = raw.trim();
    const id = templatesByName[name];
    if (!id) return `*шаблон «${name}» недоступен*`;
    return `[${escapeMarkdownLinkText(name)}](template://${id})`;
  });
}

function TaskHintCard({ hint, cardRef }) {
  return (
    <div className="chat-task-hint" ref={cardRef}>
      <div className="chat-task-hint-title">Что нужно от вас</div>
      <p>{hint.userNeed}</p>
    </div>
  );
}

export default function ChatPage({ mode, modes, conversationId, setConversationId, onConversationChange, onSelectMode, capabilityPick, freshChatToken }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [attachedFiles, setAttachedFiles] = useState([]);
  const [isDragging, setDragging] = useState(false);
  const [modeDocs, setModeDocs] = useState([]);
  const [templatesByName, setTemplatesByName] = useState({});
  // Результат роутинга для текущего ответа ассистента в режиме auto.
  // null до прихода meta-события, дальше { routed_mode, routing_reason }.
  const [routingBadge, setRoutingBadge] = useState(null);
  // Интеграция «Задача Битрикс»: доступность фичи и состояние модалки.
  const [bitrixEnabled, setBitrixEnabled] = useState(false);
  const [bitrixModalOpen, setBitrixModalOpen] = useState(false);
  const [taskHint, setTaskHint] = useState(null);
  const chatRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);
  const dragCounter = useRef(0);
  // Когда conversationId выставляется из meta-события собственного стрима,
  // не нужно перезагружать сообщения из БД — иначе незавершённый ответ
  // ассистента в локальном стейте затирается серверной выборкой (там пока
  // только сообщение пользователя).
  const skipNextHistoryFetch = useRef(false);
  // id, который выставил собственный стрим. Смена диалога — это другой id.
  const streamAssignedConvIdRef = useRef(null);
  const pendingScenarioRef = useRef(null);
  const appliedPrefillRef = useRef('');
  const taskHintRef = useRef(null);

  const [handledPickToken, setHandledPickToken] = useState(null);
  const [appliedPrefill, setAppliedPrefill] = useState('');
  appliedPrefillRef.current = appliedPrefill;

  const currentMode = modes.find(m => m.id === mode) || modes[0];

  // Реакция на клик в том же кадре, что и жест, чтобы фокус поля не терялся.
  if (capabilityPick?.token && capabilityPick.token !== handledPickToken) {
    setHandledPickToken(capabilityPick.token);
    pendingScenarioRef.current = capabilityPick.scenarioId || null;
    if (capabilityPick.prefill) {
      setTaskHint(null);
      setAppliedPrefill(capabilityPick.prefill);
      setInput(capabilityPick.prefill);
    } else {
      setTaskHint({ userNeed: capabilityPick.userNeed });
      if (appliedPrefill) {
        const previousPrefill = appliedPrefill;
        setAppliedPrefill('');
        setInput(prev => (prev === previousPrefill ? '' : prev));
      }
    }
  }

  // Смена диалога сбрасывает карточку и ожидающий сценарий.
  // Назначение id из собственного стрима — не смена диалога: карточку не трогаем.
  useEffect(() => {
    if (conversationId && conversationId === streamAssignedConvIdRef.current) return;
    streamAssignedConvIdRef.current = null;
    setTaskHint(null);
    pendingScenarioRef.current = null;
    const previousPrefill = appliedPrefillRef.current;
    setAppliedPrefill('');
    if (previousPrefill) {
      setInput(prev => (prev === previousPrefill ? '' : prev));
    }
  }, [conversationId]);

  // «Новый чат» — даже если диалог уже пустой и id не меняется.
  useEffect(() => {
    if (!freshChatToken) return;
    setTaskHint(null);
    pendingScenarioRef.current = null;
    const previousPrefill = appliedPrefillRef.current;
    setAppliedPrefill('');
    if (previousPrefill) {
      setInput(prev => (prev === previousPrefill ? '' : prev));
    }
    setMessages([]);
    setAttachedFiles([]);
  }, [freshChatToken]);

  useEffect(() => {
    if (skipNextHistoryFetch.current) {
      skipNextHistoryFetch.current = false;
      return;
    }
    if (conversationId) {
      apiGet(`/conversations/${conversationId}/messages`)
        // Сервер отдаёт check_result (snake_case) — маппим в checkResult,
        // чтобы карточка проверки контрагента отрисовалась при перезагрузке.
        .then(rows => setMessages(rows.map(m => ({ ...m, checkResult: m.check_result || null }))))
        .catch(() => {});
    } else {
      setMessages([]);
    }
  }, [conversationId]);

  useLayoutEffect(() => {
    if (!capabilityPick?.prefill || input !== capabilityPick.prefill) return;
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    const len = el.value.length;
    el.setSelectionRange(len, len);
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }, [capabilityPick, input]);

  useEffect(() => {
    if (!taskHint) return;
    requestAnimationFrame(() => {
      taskHintRef.current?.scrollIntoView({ block: 'nearest' });
    });
  }, [taskHint]);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages, loading]);

  useEffect(() => {
    apiGet('/templates')
      .then(list => {
        const map = {};
        for (const t of list) map[t.original_filename] = t.id;
        setTemplatesByName(map);
      })
      .catch(() => setTemplatesByName({}));
  }, []);

  useEffect(() => {
    if (mode === 'regulation') {
      setAttachedFiles([]);
      apiGet(`/documents/by-mode/${mode}`).then(setModeDocs).catch(() => setModeDocs([]));
    } else {
      setModeDocs([]);
    }
  }, [mode]);

  // ── Добавление файлов ─────────────────────────────────────────────────────

  const addFiles = useCallback((newFiles) => {
    const valid = Array.from(newFiles).filter(f => {
      const ext = '.' + f.name.split('.').pop().toLowerCase();
      return ALLOWED_EXT.includes(ext);
    });
    setAttachedFiles(prev => {
      const combined = [...prev, ...valid];
      return combined.slice(0, MAX_FILES);
    });
  }, []);

  const removeFile = (idx) => {
    setAttachedFiles(prev => prev.filter((_, i) => i !== idx));
  };

  const handleFileChange = (e) => {
    addFiles(e.target.files);
    e.target.value = '';
  };

  // ── Drag & Drop ───────────────────────────────────────────────────────────

  const handleDragEnter = (e) => {
    e.preventDefault();
    dragCounter.current++;
    if (mode !== 'regulation' && e.dataTransfer.items && e.dataTransfer.items.length > 0) {
      setDragging(true);
    }
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    dragCounter.current--;
    if (dragCounter.current === 0) setDragging(false);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    dragCounter.current = 0;
    setDragging(false);
    if (mode === 'regulation') return;
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files);
      e.dataTransfer.clearData();
    }
  };

  // ── Отправка ──────────────────────────────────────────────────────────────

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;

    const files = attachedFiles;
    const scenarioHint = pendingScenarioRef.current;
    const apiMessage = scenarioHint === 'template_form_navigation'
      ? 'Ищу типовую форму договора. Шаблон и типовая форма — одно и то же. ' + text
      : text;
    setInput('');
    setAppliedPrefill('');
    setAttachedFiles([]);

    const fileHint = files.length > 0
      ? '\n📎 ' + files.map(f => f.name).join(', ')
      : '';
    setMessages(prev => [
      ...prev,
      { role: 'user', content: text + fileHint },
      { role: 'assistant', content: '', routing: null, checkResult: null },
    ]);
    setLoading(true);
    setRoutingBadge(null);

    let metaSeen = false;
    let hintAccepted = false;

    const appendToLast = (chunk) => {
      setMessages(prev => {
        const next = prev.slice();
        const last = next[next.length - 1];
        if (last && last.role === 'assistant') {
          next[next.length - 1] = { ...last, content: (last.content || '') + chunk };
        }
        return next;
      });
    };

    await chatStream(
      {
        message: apiMessage,
        conversation_id: conversationId,
        mode,
        files,
        ...(scenarioHint ? { scenario_hint: scenarioHint } : {}),
      },
      {
        onMeta: ({ conversation_id, routed_mode, routing_reason }) => {
          metaSeen = true;
          hintAccepted = true;
          if (conversation_id && conversation_id !== conversationId) {
            skipNextHistoryFetch.current = true;
            streamAssignedConvIdRef.current = conversation_id;
            setConversationId(conversation_id);
            onConversationChange?.();
          }
          // Бейдж рисуем только когда сами просили auto и бэк прислал
          // распознанный режим. Если mode явный (админ выбрал вручную) —
          // бейдж не нужен.
          if (mode === 'auto' && routed_mode) {
            const badge = { routed_mode, routing_reason };
            setRoutingBadge(badge);
            setMessages(prev => {
              const next = prev.slice();
              const last = next[next.length - 1];
              if (last && last.role === 'assistant') {
                next[next.length - 1] = { ...last, routing: badge };
              }
              return next;
            });
          }
        },
        onCounterpartyCheck: (data) => {
          setMessages(prev => {
            const next = prev.slice();
            const last = next[next.length - 1];
            if (last && last.role === 'assistant') {
              next[next.length - 1] = { ...last, checkResult: data };
            }
            return next;
          });
        },
        onDelta: (delta) => {
          if (delta) appendToLast(delta);
        },
        onPartEnd: () => {
          setMessages(prev => [...prev, { role: 'assistant', content: '' }]);
        },
        onError: (msg) => {
          appendToLast(`⚠️ Ошибка: ${msg}`);
        },
        onDone: () => {
          hintAccepted = true;
        },
      },
    );

    if (hintAccepted && scenarioHint && pendingScenarioRef.current === scenarioHint) {
      pendingScenarioRef.current = null;
    }

    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = useCallback((e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imageFiles = [];
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const blob = item.getAsFile();
        if (blob) {
          const ext = (blob.type.split('/')[1] || 'png').toLowerCase();
          const safeExt = ext === 'jpeg' ? 'jpg' : ext;
          const filename = `pasted-${Date.now()}.${safeExt}`;
          imageFiles.push(new File([blob], filename, { type: blob.type }));
        }
      }
    }
    if (imageFiles.length > 0 && mode !== 'regulation') {
      e.preventDefault();
      addFiles(imageFiles);
    }
  }, [addFiles, mode]);

  const handleInputChange = (e) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  };

  // ── Экспорт ───────────────────────────────────────────────────────────────

  const handleExportMsgDocx = async (content) => {
    try { await exportMessageToDocx(content); }
    catch { alert('Не удалось создать Word-файл'); }
  };

  const handleChatExportDocx = async () => {
    try { await exportChatToDocx(messages, currentMode.label); }
    catch { alert('Не удалось создать Word-файл'); }
  };

  const handleChatExportPdf = () => exportChatToPdf(messages, currentMode.label);

  // ── Задача Битрикс ──────────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    getBitrixTaskConfig()
      .then((cfg) => { if (!cancelled) setBitrixEnabled(!!cfg.enabled); })
      .catch(() => { if (!cancelled) setBitrixEnabled(false); });
    return () => { cancelled = true; };
  }, []);

  const handleBitrixSubmit = async ({ title, description }) => {
    const blob = await buildChatDocxBlob(messages, currentMode.label);
    return createBitrixTask(conversationId, {
      title,
      description,
      blob,
      filename: chatDocxFilename(currentMode.label),
    });
  };

  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', position: 'relative' }}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragging && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 50,
          background: 'rgba(30,111,192,0.08)',
          border: '2px dashed var(--sky)',
          borderRadius: 8,
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          gap: 12, pointerEvents: 'none',
        }}>
          <div style={{
            width: 64, height: 64, borderRadius: '50%',
            background: 'var(--sky-soft)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Upload size={28} color="var(--sky)" />
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--sky)' }}>
            Перетащите файлы сюда
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
            PDF, Word, TXT, MD, изображения · до {MAX_FILES} файлов · до 10 МБ каждый
          </div>
        </div>
      )}

      {/* Header */}
      <div className="main-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <currentMode.icon size={18} />
          {currentMode.label}
        </h2>
        <div className="header-actions">
          {messages.length > 0 && (
            <>
              <button className="btn btn-ghost btn-sm" onClick={handleChatExportPdf} title="Экспорт чата в PDF / Печать">
                <FileText size={13} /> PDF/Печать
              </button>
              <button className="btn btn-ghost btn-sm" onClick={handleChatExportDocx} title="Экспорт чата в Word">
                <FileDown size={13} /> Word
              </button>
              {bitrixEnabled && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setBitrixModalOpen(true)}
                  disabled={!conversationId}
                  title={conversationId ? 'Создать задачу в Битрикс24 с историей чата' : 'Отправьте сообщение, чтобы создать задачу'}
                >
                  <ClipboardList size={13} /> Задача Битрикс
                </button>
              )}
            </>
          )}
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => onSelectMode?.()}
          >
            <Plus size={14} /> Новый чат
          </button>
        </div>
      </div>

      <BitrixTaskModal
        open={bitrixModalOpen}
        defaultTitle={currentMode.label}
        author={localStorage.getItem('username') || ''}
        onClose={() => setBitrixModalOpen(false)}
        onSubmit={handleBitrixSubmit}
      />

      {/* Chat area */}
      <div className="chat-area" ref={chatRef}>
        {messages.length === 0 && !loading && (
          <div className="chat-welcome" style={taskHint ? { maxWidth: 720 } : undefined}>
            <div className="welcome-icon">⚖</div>
            <h3>{currentMode.label}</h3>
            {mode === 'auto' && !taskHint && (
              <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 8, maxWidth: 680 }}>
                Просто опишите задачу или прикрепите файл (текст, картинка, pdf) — я сам пойму, что нужно: ответить на правовой вопрос, проверить рекламу, разобрать договор или найти ответ в регламенте.
              </p>
            )}
            {taskHint && messages.length === 0 && (
              <TaskHintCard hint={taskHint} cardRef={taskHintRef} />
            )}
            {mode === 'regulation' && modeDocs.length > 0 && (
              <div style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 8, maxWidth: 680, textAlign: 'left' }}>
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {modeDocs.map((name, i) => (
                    <li key={i} style={{ marginBottom: 2 }}>{name.replace(/\.md$/i, '')}</li>
                  ))}
                </ul>
              </div>
            )}
            {mode !== 'regulation' && (
              <p style={{ color: 'var(--text-dim)', fontSize: 13, marginTop: 12 }}>
                Перетащите файлы в окно или прикрепите через кнопку
              </p>
            )}
          </div>
        )}
        {messages.map((msg, i) => {
          const routedMode = msg.routing?.routed_mode;
          const routedMeta = routedMode ? modes.find(m => m.id === routedMode) : null;
          return (
          <div key={i} className={`message ${msg.role}`}>
            <div className="message-avatar">
              {msg.role === 'assistant' ? '⚖' : '👤'}
            </div>
            <div className="message-body">
              {msg.role === 'assistant' && routedMeta && (
                <div style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '2px 10px', marginBottom: 8,
                  background: 'var(--sky-soft)',
                  border: '1px solid rgba(30,111,192,.25)',
                  borderRadius: 12,
                  fontSize: 12, color: 'var(--sky)', fontWeight: 600,
                }} title={msg.routing.routing_reason || ''}>
                  <routedMeta.icon size={12} />
                  Режим: {routedMeta.label}
                  {msg.routing.routing_reason && (
                    <span style={{ fontWeight: 400, color: 'var(--text-dim)', marginLeft: 4 }}>
                      — {msg.routing.routing_reason}
                    </span>
                  )}
                </div>
              )}
              {msg.role === 'assistant' ? (
                <>
                  {msg.checkResult && <CheckResponse data={msg.checkResult} />}
                  <ReactMarkdown
                    urlTransform={url => url}
                    components={{
                      a: ({ node, href, children, ...props }) => {
                        if (href && href.startsWith('template://')) {
                          const id = href.slice('template://'.length);
                          const name = Array.isArray(children) ? children.join('') : String(children || '');
                          return (
                            <button
                              type="button"
                              onClick={() => downloadFile(`/templates/${id}/download`, name)}
                              style={{
                                display: 'inline-flex', alignItems: 'center', gap: 6,
                                padding: '4px 10px', margin: '2px 4px 2px 0',
                                background: 'var(--sky-soft)',
                                border: '1px solid rgba(30,111,192,.35)',
                                borderRadius: 16, fontSize: 13,
                                color: 'var(--sky)', fontWeight: 600, cursor: 'pointer',
                              }}
                              title={`Скачать ${name}`}
                            >
                              <Download size={14} />
                              {name}
                            </button>
                          );
                        }
                        return <a href={href} {...props} target="_blank" rel="noopener noreferrer">{children}</a>;
                      },
                    }}
                  >{renderTemplateMarkers(
                    msg.content.replace(/<!--\s*DOC_(START|END)\s*-->/gi, ''),
                    templatesByName,
                  )}</ReactMarkdown>
                  {/<!--\s*DOC_START\s*-->[\s\S]*?<!--\s*DOC_END\s*-->/i.test(msg.content) && (
                    <button
                      type="button"
                      onClick={() => handleExportMsgDocx(msg.content)}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 6,
                        marginTop: 12, padding: '6px 12px',
                        background: 'var(--sky-soft)',
                        border: '1px solid rgba(30,111,192,.35)',
                        borderRadius: 16, fontSize: 13,
                        color: 'var(--sky)', fontWeight: 600, cursor: 'pointer',
                      }}
                      title="Скачать готовый документ в формате Word"
                    >
                      <FileDown size={14} /> Скачать .docx
                    </button>
                  )}
                </>
              ) : (
                <p style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</p>
              )}
            </div>
          </div>
        );
        })}
        {taskHint && messages.length > 0 && (
          <TaskHintCard hint={taskHint} cardRef={taskHintRef} />
        )}
        {loading && (
          <div className="message assistant">
            <div className="message-avatar">⚖</div>
            <div className="message-body">
              <div className="typing-indicator"><span /><span /><span /></div>
            </div>
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="chat-input-area">

        {/* Прикреплённые файлы */}
        {attachedFiles.length > 0 && (
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 6,
            marginBottom: 10, maxWidth: 800, margin: '0 auto 10px',
          }}>
            {attachedFiles.map((f, idx) => (
              <div key={idx} style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '4px 10px 4px 8px',
                background: 'var(--sky-soft)',
                border: '1px solid rgba(30,111,192,.2)',
                borderRadius: 20,
                fontSize: 12, color: 'var(--sky)', fontWeight: 600,
                maxWidth: 200,
              }}>
                <Paperclip size={12} style={{ flexShrink: 0 }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {f.name}
                </span>
                <button
                  type="button"
                  onClick={() => removeFile(idx)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, lineHeight: 1, flexShrink: 0 }}
                >
                  <X size={12} color="var(--sky)" />
                </button>
              </div>
            ))}
            {attachedFiles.length < MAX_FILES && (
              <div style={{ fontSize: 11, color: 'var(--text-dim)', alignSelf: 'center', marginLeft: 2 }}>
                ещё {MAX_FILES - attachedFiles.length}
              </div>
            )}
          </div>
        )}

        <div className="chat-input-wrap">
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_EXT.join(',')}
            multiple
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          {mode !== 'regulation' && (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => fileInputRef.current?.click()}
              title={`Прикрепить файлы (до ${MAX_FILES})`}
              style={{ alignSelf: 'flex-end', padding: '8px', position: 'relative' }}
            >
              <Paperclip size={16} />
              {attachedFiles.length > 0 && (
                <span style={{
                  position: 'absolute', top: 2, right: 2,
                  width: 14, height: 14, borderRadius: '50%',
                  background: 'var(--sky)', color: '#fff',
                  fontSize: 9, fontWeight: 700,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {attachedFiles.length}
                </span>
              )}
            </button>
          )}
          <textarea
            ref={inputRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              mode === 'auto'
                ? 'Напишите вопрос или прикрепите файл — я сам пойму, что нужно сделать.'
                : mode === 'ad_check'
                  ? 'Вложите файл или укажите текст креатива, укажите чем является продукт (лекарственный препарат ОТС или Rx, БАД, косметическое средство) и укажите как вы планируете использовать данный креатив (баннер в интернет, рассылка по email, печатная листовка и т.п.)'
                  : mode === 'regulation'
                    ? 'Задай свой вопрос и я посмотрю что есть в наших регламентах'
                    : mode === 'consult'
                      ? 'Задайте свой вопрос. Помогу ответить на несложные правовые вопросы, найти документы и реквизиты компании и типовые договоры.'
                      : mode === 'document_gen'
                        ? 'Помогу составить резюме (summary) по договору, найду ответы на ваши вопросы по договору, подготовлю претензию, письмо, дополнительное соглашение, уведомление о расторжении. Помогу найти реквизиты, документы для контрагента, типовые формы договоров, внутренние регламенты'
                        : `Задайте вопрос в режиме «${currentMode.label}»...`
            }
            rows={2}
          />
          <button
            className="btn btn-primary"
            onClick={handleSend}
            disabled={!input.trim() || loading}
            style={{ alignSelf: 'flex-end' }}
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
