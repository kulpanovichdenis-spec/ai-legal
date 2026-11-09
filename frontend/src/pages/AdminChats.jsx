import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { apiGet } from '../api.js';
import { MessageSquare, ChevronRight } from 'lucide-react';

const MODE_LABELS = {
  consult: 'Консультация',
  ad_check: 'Проверка рекламы',
  document_gen: 'Документы',
  regulation: 'Регламенты',
};

export default function AdminChats() {
  const [chats, setChats] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const messagesRef = useRef(null);

  useEffect(() => {
    apiGet('/admin/chats').then(setChats).catch(() => {});
  }, []);

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSelect = async (chat) => {
    setSelected(chat);
    setMessages([]);
    setLoadingMsgs(true);
    try {
      setMessages(await apiGet(`/admin/chats/${chat.id}/messages`));
    } catch {}
    finally { setLoadingMsgs(false); }
  };

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Список чатов */}
      <div style={{
        width: 300, flexShrink: 0, borderRight: '1px solid var(--border)',
        overflowY: 'auto', display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ padding: '16px 16px 8px', fontWeight: 700, fontSize: 15, borderBottom: '1px solid var(--border)' }}>
          Все чаты ({chats.length})
        </div>
        {chats.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: 13, padding: 16 }}>Чатов пока нет</p>
        ) : (
          chats.map(c => (
            <div
              key={c.id}
              onClick={() => handleSelect(c)}
              style={{
                padding: '12px 16px', cursor: 'pointer', borderBottom: '1px solid var(--border)',
                background: selected?.id === c.id ? 'var(--accent-soft)' : 'transparent',
                transition: 'background 0.15s',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
                <div style={{
                  fontWeight: 600, fontSize: 13,
                  color: selected?.id === c.id ? 'var(--accent)' : 'var(--text)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1,
                }}>
                  {c.title || 'Без названия'}
                </div>
                <ChevronRight size={14} color="var(--text-dim)" />
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 3 }}>
                👤 {c.username} · {MODE_LABELS[c.mode] || c.mode}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>
                {new Date(c.updated_at).toLocaleString('ru-RU')}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Сообщения */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {selected ? (
          <>
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid var(--border)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <MessageSquare size={16} color="var(--accent)" />
              <span style={{ fontWeight: 600, fontSize: 14 }}>{selected.title || 'Без названия'}</span>
              <span style={{ fontSize: 12, color: 'var(--text-dim)', marginLeft: 4 }}>
                {selected.username} · {MODE_LABELS[selected.mode] || selected.mode}
              </span>
            </div>

            <div ref={messagesRef} style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
              {loadingMsgs ? (
                <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Загрузка...</p>
              ) : messages.length === 0 ? (
                <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Сообщений нет</p>
              ) : (
                messages.map((msg, i) => (
                  <div key={i} className={`message ${msg.role}`} style={{ marginBottom: 16 }}>
                    <div className="message-avatar">
                      {msg.role === 'assistant' ? '⚖' : '👤'}
                    </div>
                    <div className="message-body">
                      {msg.role === 'assistant' ? (
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                      ) : (
                        <p>{msg.content}</p>
                      )}
                      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                        {new Date(msg.created_at).toLocaleString('ru-RU')}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </>
        ) : (
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--text-dim)', fontSize: 14,
          }}>
            Выберите чат для просмотра
          </div>
        )}
      </div>
    </div>
  );
}
