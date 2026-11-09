import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate, NavLink, useNavigate, useLocation } from 'react-router-dom';
import LoginPage from './pages/LoginPage.jsx';
import ChatPage from './pages/ChatPage.jsx';
import AdminDocuments from './pages/AdminDocuments.jsx';
import AdminSkills from './pages/AdminSkills.jsx';
import AdminUsers from './pages/AdminUsers.jsx';
import AdminModes from './pages/AdminModes.jsx';
import AdminChats from './pages/AdminChats.jsx';
import AdminProjects from './pages/AdminProjects.jsx';
import DashboardPage from './pages/DashboardPage.jsx';
import ProjectsListPage from './pages/ProjectsListPage.jsx';
import ProjectPage from './pages/ProjectPage.jsx';
import {
  MessageSquare, FileText, Shield, Settings, Users, LogOut, Scale, BookOpen, PenTool, AlertTriangle, SlidersHorizontal, MessagesSquare, Plus, Trash2, Pencil, Sparkles, Presentation, Briefcase
} from 'lucide-react';
import { apiGet, apiDelete, apiPatch, logoutClearStorage, resetUnauthorizedFlag } from './api.js';
import { ConfirmModal, PromptModal } from './components/Modal.jsx';

// Виртуальный режим «Помощник» — единственный, который видит обычный
// пользователь. Бэкенд принимает mode="auto" и сам определяет реальный
// режим работы (consult/ad_check/document_gen/regulation).
const AUTO_MODE = {
  id: 'auto',
  label: 'Помощник',
  icon: Sparkles,
  desc: 'Сам выбирает нужный режим по вашему вопросу',
  capabilities: [
    {
      key: 'ad',
      parts: [
        {
          id: 'ad_check',
          label: 'проверять креативы на соответствие ФЗ О рекламе',
          scenarioId: 'ad_check_general',
          userNeed: 'Прикрепите креатив (текст, картинка или pdf) или вставьте текст. Напишите, чем является продукт (ЛС ОТС или Rx, БАД, косметика) и где он будет использоваться: баннер, email, листовка и т.п.',
        },
      ],
    },
    {
      key: 'draft',
      parts: [
        { text: 'готовить ' },
        {
          id: 'contract_summary',
          label: 'резюме по договору',
          scenarioId: 'contract_analysis',
          userNeed: 'Прикрепите договор. Напишите, что вынести в резюме: договор целиком или отдельные блоки — предмет, сроки, ответственность, расторжение.',
        },
        { text: ', ' },
        {
          id: 'claim',
          label: 'претензии',
          scenarioId: 'claim_drafting',
          userNeed: 'Прикрепите договор и документы по нарушению. Напишите, кто нарушил, в чём нарушение, чего требуете и в какой срок.',
        },
        { text: ', ' },
        {
          id: 'notice',
          label: 'уведомления',
          scenarioId: 'notice_drafting',
          userNeed: 'Прикрепите договор. Напишите, о чём уведомление, кому оно и какие сроки в договоре.',
        },
        { text: ', ' },
        {
          id: 'addendum',
          label: 'доп. соглашения',
          scenarioId: 'document_from_contract',
          userNeed: 'Прикрепите договор. Напишите, какие условия меняются: предмет, цена, сроки, стороны или другие пункты.',
        },
        { text: ', ' },
        {
          id: 'letter',
          label: 'письма',
          scenarioId: 'notice_drafting',
          userNeed: 'Напишите, кому письмо, по какому договору и что сообщить или запросить. Договор или переписку прикрепите, если они есть.',
        },
        { text: ', ' },
        {
          id: 'disagreement',
          label: 'протоколы разногласий',
          scenarioId: 'document_from_contract',
          userNeed: 'Прикрепите проект договора или редакцию контрагента. Напишите, с какими пунктами не согласны и какую формулировку предлагаете.',
        },
      ],
    },
    {
      key: 'find',
      parts: [
        { text: 'находить ' },
        {
          id: 'templates',
          label: 'типовые формы договоров',
          scenarioId: 'template_form_navigation',
          userNeed: 'Напишите, какой договор нужен и для какой ситуации. Файл не обязателен.',
        },
        { text: ', ' },
        {
          id: 'requisites',
          label: 'реквизиты',
          scenarioId: 'legal_consultation',
          prefill: 'Пришли мне карточку компании с актуальными реквизитами',
        },
        { text: ' и ' },
        {
          id: 'counterparty_docs',
          label: 'документы для контрагентов',
          scenarioId: 'legal_consultation',
          prefill: 'Пришли мне документы для контрагентов',
        },
      ],
    },
    {
      key: 'regulation',
      parts: [
        {
          id: 'regulation',
          label: 'искать ответы во внутренних регламентах',
          scenarioId: 'regulation_lookup',
          userNeed: 'Напишите вопрос своими словами. Если знаете тему, назовите её: договоры, комплаенс, ЭДО, закупки.',
        },
        { text: ' компании' },
      ],
    },
  ],
  capabilitiesHint: 'Просто опиши задачу и прикрепи документ.',
};

const MANUAL_MODES = [
  {
    id: 'ad_check',
    label: 'Проверка рекламы',
    icon: AlertTriangle,
    desc: 'Анализ креативов по ФЗ-38',
    fullDesc: 'Проверю ваш креатив. Вложите файл (текст, картинка, pdf) или отправьте текст креатива. Не забудьте указать чем является продукт и как вы планируете использовать креатив.',
  },
  {
    id: 'document_gen',
    label: 'Документы',
    icon: PenTool,
    desc: 'Уведомления, соглашения, письма',
    fullDesc: 'Помогу составить резюме (summary) по договору, подготовлю претензию, письмо, дополнительное соглашение, уведомление о расторжении. Помогу найти реквизиты, документы для контрагента, типовые формы договоров, внутренние регламенты.',
  },
  {
    id: 'regulation',
    label: 'Регламенты',
    icon: BookOpen,
    desc: 'Поиск по внутренним документам',
    fullDesc: 'Помогу найти ответ в регламентах. Список загруженных регламентов вы видите справа.',
  },
  {
    id: 'consult',
    label: 'Консультация',
    icon: MessageSquare,
    desc: 'Вопросы по праву и договорам',
    fullDesc: 'Отвечу на вопросы по гражданскому, договорному и фармацевтическому праву. Помогу найти реквизиты, документы для контрагента, типовые формы договоров, внутренние регламенты.',
  },
  {
    id: 'presentation',
    label: 'Презентация для бизнеса',
    icon: Presentation,
    desc: 'Брифы и саммари юр.работы для бизнеса',
    fullDesc: 'Помогу подготовить презентацию результатов юридической работы для бизнес-заказчика: краткое резюме, риски, что сделано, что нужно от заказчика дальше. Режим доступен только администратору.',
  },
];

// Полный набор используется для отрисовки иконок истории чатов и
// карточек сообщений — там встречаются все 4 реальных режима + auto.
const MODES = [AUTO_MODE, ...MANUAL_MODES];

function readStoredUser() {
  const token = localStorage.getItem('token');
  const role = localStorage.getItem('role');
  const username = localStorage.getItem('username');
  if (token && username) return { token, role, username };
  return null;
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();

  const [user, setUser] = useState(readStoredUser);
  const [mode, setMode] = useState('auto');
  const [conversationId, setConversationId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [historyTick, setHistoryTick] = useState(0);

  // Модальные диалоги — заменили window.confirm/prompt.
  const [confirmState, setConfirmState] = useState(null);
  const [renameState, setRenameState] = useState(null);
  const [capabilityPick, setCapabilityPick] = useState(null);
  const [freshChatToken, setFreshChatToken] = useState(0);

  const refreshHistory = () => setHistoryTick(t => t + 1);

  // Глобальная обработка 401 от api.js — выходим и идём на /login.
  useEffect(() => {
    function onUnauthorized() {
      setUser(null);
      navigate('/login', { replace: true });
    }
    window.addEventListener('app:unauthorized', onUnauthorized);
    return () => window.removeEventListener('app:unauthorized', onUnauthorized);
  }, [navigate]);

  useEffect(() => {
    if (!user) return;
    apiGet('/conversations').then(setConversations).catch(() => setConversations([]));
  }, [user, historyTick, conversationId, location.pathname]);

  const handleDeleteConversation = (id, currentTitle) => {
    setConfirmState({
      id,
      title: 'Удалить чат?',
      message: `Чат «${currentTitle || 'Без названия'}» и вся его история будут удалены безвозвратно.`,
      onConfirm: async () => {
        try {
          await apiDelete(`/conversations/${id}`);
          if (conversationId === id) setConversationId(null);
          refreshHistory();
        } catch (err) {
          alert('Не удалось удалить: ' + err.message);
        }
      },
    });
  };

  const handleRenameConversation = (id, currentTitle) => {
    setRenameState({
      id,
      currentTitle: currentTitle || '',
      onSubmit: async (next) => {
        try {
          await apiPatch(`/conversations/${id}`, { title: next });
          refreshHistory();
        } catch (err) {
          alert('Не удалось переименовать: ' + err.message);
        }
      },
    });
  };

  const openConversation = (c) => {
    // У уже открытого диалога режим зафиксирован — используем его, чтобы
    // правильно показать заголовок и плейсхолдер; бэкенд всё равно
    // подставит mode из БД.
    setMode(c.mode || 'auto');
    setConversationId(c.id);
    navigate('/chat');
  };

  const handleLogin = (data) => {
    localStorage.setItem('token', data.token);
    localStorage.setItem('role', data.role);
    localStorage.setItem('username', data.username);
    resetUnauthorizedFlag();
    setUser(data);
    // После логина — на соответствующий стартовый экран
    if (data.role === 'dashboard') navigate('/dashboard', { replace: true });
    else navigate('/chat', { replace: true });
  };

  const handleLogout = () => {
    logoutClearStorage();
    setUser(null);
    setConversationId(null);
    navigate('/login', { replace: true });
  };

  // ── Публичные маршруты (без авторизации) ────────────────────────────────
  // Дашборд проектов и страница проекта доступны всем во внутреннем
  // контуре без логина. Размещены ДО auth-гардов, чтобы не редиректить
  // в /login. Логированный пользователь тоже может зайти — auth-state
  // используется только для подсветки админских ссылок при их наличии.
  const _path = location.pathname;
  if (_path === '/projects' || _path.startsWith('/projects/')) {
    return (
      <Routes>
        <Route path="/projects" element={<ProjectsListPage />} />
        <Route path="/projects/:slug" element={<ProjectPage />} />
      </Routes>
    );
  }

  // ── Routing guards ──────────────────────────────────────────────────────
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage onLogin={handleLogin} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  if (user.role === 'dashboard') {
    return (
      <Routes>
        <Route path="/dashboard" element={<DashboardPage user={user} onLogout={handleLogout} />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    );
  }

  const isAdmin = user.role === 'admin';

  const startNewChat = (newMode) => {
    setMode(newMode || 'auto');
    setConversationId(null);
    setCapabilityPick(null);
    setFreshChatToken(t => t + 1);
    navigate('/chat');
  };

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>
            <span className="icon"><Scale size={16} /></span>
            Юрист AI
          </h1>
        </div>

        <nav className="sidebar-nav">
          {isAdmin ? (
            <div className="nav-section">
              <div className="nav-section-label">Режимы</div>
              {MODES.map(m => (
                <button
                  key={m.id}
                  className={`nav-item ${location.pathname === '/chat' && mode === m.id ? 'active' : ''}`}
                  onClick={() => startNewChat(m.id)}
                >
                  <m.icon size={18} />
                  {m.label}
                </button>
              ))}
            </div>
          ) : (
            location.pathname === '/chat' && (
              <div className="nav-section">
                <div className="mode-description">
                  <div className="mode-description-title">Что я умею:</div>
                  <ul className="mode-description-list">
                    {AUTO_MODE.capabilities.map(line => (
                      <li key={line.key}>
                        {line.parts.map((part, index) => (
                          part.label ? (
                            <span
                              key={part.id}
                              role="button"
                              tabIndex={0}
                              className="mode-capability"
                              onClick={() => setCapabilityPick({ ...part, token: Date.now() })}
                              onKeyDown={(e) => {
                                if (e.key !== 'Enter' && e.key !== ' ') return;
                                e.preventDefault();
                                setCapabilityPick({ ...part, token: Date.now() });
                              }}
                            >{part.label}</span>
                          ) : (
                            <span key={`${line.key}-${index}`}>{part.text}</span>
                          )
                        ))}
                      </li>
                    ))}
                  </ul>
                  <div className="mode-description-hint">
                    {AUTO_MODE.capabilitiesHint}
                  </div>
                </div>
              </div>
            )
          )}

          {!isAdmin && (
            <div className="nav-section">
              <div className="nav-section-label">История чатов</div>
              <button
                className="nav-item nav-item-newchat"
                onClick={() => startNewChat()}
              >
                <Plus size={16} />
                Новый чат
              </button>
              {conversations.length === 0 && (
                <div className="history-empty">Здесь появятся ваши чаты.</div>
              )}
              {conversations.map(c => {
                const modeMeta = MODES.find(m => m.id === c.mode);
                const Icon = modeMeta?.icon || MessageSquare;
                const title = c.title || 'Без названия';
                return (
                  <div
                    key={c.id}
                    className={`history-item ${conversationId === c.id ? 'active' : ''}`}
                    onClick={() => openConversation(c)}
                    title={title}
                  >
                    <Icon size={14} className="history-icon" />
                    <span className="history-title">{title}</span>
                    <button
                      className="history-action"
                      onClick={(e) => { e.stopPropagation(); handleRenameConversation(c.id, c.title); }}
                      title="Переименовать"
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      className="history-action history-action-danger"
                      onClick={(e) => { e.stopPropagation(); handleDeleteConversation(c.id, c.title); }}
                      title="Удалить"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {isAdmin && (
            <div className="nav-section">
              <div className="nav-section-label">Настройки</div>
              <NavLink to="/admin/chats" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                <MessagesSquare size={18} />
                Чаты
              </NavLink>
              <NavLink to="/admin/documents" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                <FileText size={18} />
                Документы
              </NavLink>
              <NavLink to="/admin/skills" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                <Shield size={18} />
                Скиллы
              </NavLink>
              <NavLink to="/admin/users" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                <Users size={18} />
                Пользователи
              </NavLink>
              <NavLink to="/admin/modes" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                <SlidersHorizontal size={18} />
                Инструкции
              </NavLink>
              <NavLink to="/admin/projects" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                <Briefcase size={18} />
                Проекты на дашборде
              </NavLink>
            </div>
          )}
        </nav>

        <div className="sidebar-footer">
          {!isAdmin && (
            <>
              <div className="sidebar-help">
                По сложным правовым вопросам обращайтесь в юридическую службу вашей организации.
              </div>
            </>
          )}
          <div className="user-badge">
            <div className="avatar">{user.username[0].toUpperCase()}</div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{user.username}</div>
              <div className="role">{isAdmin ? 'Администратор' : 'Сотрудник'}</div>
            </div>
            <button
              onClick={handleLogout}
              style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-dim)', cursor: 'pointer' }}
              aria-label="Выйти"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/login" element={<Navigate to="/chat" replace />} />
          <Route
            path="/chat"
            element={
              <ChatPage
                mode={mode}
                modes={MODES}
                conversationId={conversationId}
                setConversationId={setConversationId}
                onSelectMode={startNewChat}
                onConversationChange={refreshHistory}
                capabilityPick={capabilityPick}
                freshChatToken={freshChatToken}
              />
            }
          />
          {isAdmin && <Route path="/admin/documents" element={<AdminDocuments />} />}
          {isAdmin && <Route path="/admin/skills" element={<AdminSkills />} />}
          {isAdmin && <Route path="/admin/users" element={<AdminUsers />} />}
          {isAdmin && <Route path="/admin/modes" element={<AdminModes />} />}
          {isAdmin && <Route path="/admin/chats" element={<AdminChats />} />}
          {isAdmin && <Route path="/admin/projects" element={<AdminProjects />} />}
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </main>

      <ConfirmModal
        open={!!confirmState}
        onClose={() => setConfirmState(null)}
        onConfirm={() => confirmState?.onConfirm?.()}
        title={confirmState?.title}
        message={confirmState?.message}
        confirmText="Удалить"
        danger
      />

      <PromptModal
        open={!!renameState}
        onClose={() => setRenameState(null)}
        onSubmit={(value) => renameState?.onSubmit?.(value)}
        title="Переименовать чат"
        label="Новое название"
        defaultValue={renameState?.currentTitle || ''}
        submitText="Сохранить"
      />
    </div>
  );
}
