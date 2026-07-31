// src/config/constants.js

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || "ws://127.0.0.1:8000";
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_WEB_CLIENT_ID;

if (!API_BASE_URL) {
  throw new Error("VITE_API_BASE_URL is not set");
}

export const ENV = {
  API_BASE_URL,
  WS_BASE_URL,
  GOOGLE_CLIENT_ID,
};

export const API = {
  BASE_URL: API_BASE_URL,

  AUTH: {
    CSRF: "/api/v1/auth/csrf/",
    SIGNUP: "/api/v1/auth/signup/",
    SIGNIN: "/api/v1/auth/signin/",
    GOOGLE: "/api/v1/auth/google/",
    REFRESH: "/api/v1/auth/refresh/",
    SIGNOUT: "/api/v1/auth/signout/",
    ME: "/api/v1/auth/me/",
    PROFILE: "/api/v1/auth/profile/",
    EXPORT_DATA: "/api/v1/auth/export-data/",

    VERIFY_EMAIL: "/api/v1/auth/verify-email/",
    RESEND_VERIFICATION_CODE: "/api/v1/auth/resend-verification-code/",

    REQUEST_PASSWORD_RESET: "/api/v1/auth/request-password-reset/",
    CONFIRM_PASSWORD_RESET: "/api/v1/auth/confirm-password-reset/",

    CHANGE_PASSWORD: "/api/v1/auth/change-password/",
    DELETE_ACCOUNT: "/api/v1/auth/delete-account/",

    REQUEST_EMAIL_CHANGE: "/api/v1/auth/request-email-change/",
    CONFIRM_EMAIL_CHANGE: "/api/v1/auth/confirm-email-change/",
  },

  GUEST: {
    ENSURE_SESSION: "/api/v1/guest/session/",
    MIGRATE_SESSION: "/api/v1/guest/session/migrate/",
    WS_TICKET: "/api/v1/guest/ws-ticket/",
  },

  CHARACTERS: {
    BASE: "/api/v1/characters/",
    PENDING: "/api/v1/characters/pending/",
    CREATE: "/api/v1/characters/create/",
    GET_BY_ID: (id) => `/api/v1/characters/${id}/`,
    GET_BY_SLUG: (slug) => `/api/v1/characters/slug/${slug}/`,
    UPDATE: (id) => `/api/v1/characters/${id}/update/`,
    MY_CHARACTERS: "/api/v1/characters/my-characters/",

    SET_ACTIVE: (id) => `/api/v1/characters/${id}/set-active/`,
    SET_FEATURED: (id) => `/api/v1/characters/${id}/set-featured/`,
    SET_NSFW: (id) => `/api/v1/characters/${id}/set-nsfw/`,

    APPROVE: (id) => `/api/v1/characters/${id}/approve/`,
    REJECT: (id) => `/api/v1/characters/${id}/reject/`,

    DELETE: (id) => `/api/v1/characters/${id}/delete/`,

    GENERATE_AVATAR: (id) => `/api/v1/characters/${id}/generate-avatar/`,
    COMPLETE_SETUP: (id) => `/api/v1/characters/${id}/complete-setup/`,
    FLAG: (id) => `/api/v1/characters/${id}/flag/`,
    FLAGGED: "/api/v1/characters/flagged/",
    CLEAR_FLAGS: (id) => `/api/v1/characters/${id}/clear-flags/`,

    GALLERY: {
      LIST: (characterId) => `/api/v1/characters/${characterId}/gallery/`,
      UPLOAD: (characterId) =>
        `/api/v1/characters/${characterId}/gallery/upload/`,
      GENERATE: (characterId) =>
        `/api/v1/characters/${characterId}/gallery/generate/`,
      DELETE: (imageId) => `/api/v1/characters/gallery/${imageId}/delete/`,
    },
  },

  PRICING: {
    GET: "/api/v1/pricing/",
  },

  OCCUPATIONS: {
    LIST: "/api/v1/occupations/",
    CREATE: "/api/v1/occupations/create/",
  },

  HOBBIES: {
    LIST: "/api/v1/hobbies/",
    CREATE: "/api/v1/hobbies/create/",
  },

  PERSONALITIES: {
    LIST: "/api/v1/personalities/",
    CREATE: "/api/v1/personalities/create/",
    DELETE: (id) => `/api/v1/personalities/${id}/delete/`,
  },

  CONVERSATIONS: {
    START: "/api/v1/conversations/start/",
    LIST: "/api/v1/conversations/",
    UNREAD: "/api/v1/conversations/unread/",
    MARK_READ: (conversationId) =>
      `/api/v1/conversations/${conversationId}/read/`,
    MESSAGE_HISTORY: (conversationId) =>
      `/api/v1/conversations/${conversationId}/messages-list/`,
    SEND_MESSAGE: (conversationId) =>
      `/api/v1/conversations/${conversationId}/messages/`,
    GENERATE_IMAGE: (conversationId) =>
      `/api/v1/conversations/${conversationId}/generate-image/`,
    RESET: (conversationId) => `/api/v1/conversations/${conversationId}/reset/`,
    DELETE_MESSAGE: (conversationId, messageId) =>
      `/api/v1/conversations/${conversationId}/messages/${messageId}/delete/`,
  },

  TOKENS: {
    BALANCE: "/api/v1/tokens/balance/",
  },

  MESSAGES: {
    TODAY_COUNT: "/api/v1/messages/today-count/",
  },

  MESSAGE_FLAGS: {
    SUBMIT: "/api/v1/messages/flag/",
    ADMIN_LIST: "/api/v1/admin/message-flags/",
    ADMIN_REVIEW: (flagId) => `/api/v1/admin/message-flags/${flagId}/review/`,
    FOR_CONVERSATION: (convId) =>
      `/api/v1/conversations/${convId}/flagged-messages/`,
  },

  PAYSTACK: {
    INITIATE: "/api/v1/paystack/pay/",
    VERIFY: "/api/v1/paystack/verify/",
  },

  PACKAGES: "/api/v1/packages/",

  SPARK_POOL: {
    // Not "/api/v1/spark_pool/": that path is taken by an older accounts-only
    // view, so every guest balance request sent there came back 401 and the
    // counter had nothing to correct itself with.
    BALANCE: "/api/v1/spark_pool/balance/",
    TRANSACTIONS: "/api/v1/spark_pool/transactions/",
    TRANSACTIONS_PAGINATED: "/api/v1/spark_pool/transactions/paginated/",
  },

  SUBSCRIPTION: "/api/v1/subscription/",

  ADMIN_PACKAGES: {
    SPARK_PACKAGES: {
      LIST: "/api/v1/admin/packages/spark-packages/",
      CREATE: "/api/v1/admin/packages/spark-packages/create/",
      UPDATE: (slug) => `/api/v1/admin/packages/spark-packages/${slug}/update/`,
      DELETE: (slug) => `/api/v1/admin/packages/spark-packages/${slug}/delete/`,
    },
    SUBSCRIPTION_PLANS: {
      LIST: "/api/v1/admin/packages/subscription-plans/",
      CREATE: "/api/v1/admin/packages/subscription-plans/create/",
      UPDATE: (slug) =>
        `/api/v1/admin/packages/subscription-plans/${slug}/update/`,
      DELETE: (slug) =>
        `/api/v1/admin/packages/subscription-plans/${slug}/delete/`,
    },
  },

  FEEDBACK: {
    SUBMIT: "/api/v1/feedback/submit/",
    ADMIN_LIST: "/api/v1/feedback/admin/",
    ADMIN_MARK_READ: (feedbackId) =>
      `/api/v1/feedback/admin/${feedbackId}/read/`,
    ADMIN_DELETE: (feedbackId) =>
      `/api/v1/feedback/admin/${feedbackId}/delete/`,
  },

  STAFF: {
    LIST: "/api/v1/auth/staff/",
  },

  SUPPORT: {
    TICKETS: "/api/v1/support/tickets/",
    CREATE: "/api/v1/support/tickets/create/",
    TICKET_DETAIL: (ticketId) => `/api/v1/support/tickets/${ticketId}/`,
    REPLY: (ticketId) => `/api/v1/support/tickets/${ticketId}/reply/`,
    UPDATE_STATUS: (ticketId) => `/api/v1/support/tickets/${ticketId}/status/`,
    UNREAD_COUNT: "/api/v1/support/unread-count/",
    MARK_READ: (ticketId) => `/api/v1/support/tickets/${ticketId}/mark-read/`,
  },

  MEMORY: {
    CLEAR: (characterId) => `/api/v1/memory/clear/${characterId}/`,
  },

  // ─────────────────────────────────────────────────────────────
  // Proactive Chat (Interactive Chatting)
  // ─────────────────────────────────────────────────────────────
  PROACTIVE_CHAT: {
    PREFERENCE: (characterId) =>
      `/api/v1/proactive-chat/preference/${characterId}/`,
    UPDATE: (characterId) =>
      `/api/v1/proactive-chat/preference/${characterId}/update/`,
  },

  PING: "/api/ping/",
};

// Guests connect with a single-use ticket rather than their credential: a
// WebSocket URL is recorded by every proxy it crosses, so whatever is in it must
// be worthless a few seconds later.
export const WS = {
  CHAT: (conversationId, authValue, isGuest = false) => {
    const param = isGuest
      ? `ticket=${encodeURIComponent(authValue)}`
      : `token=${encodeURIComponent(authValue)}`;
    return `${WS_BASE_URL}/ws/chat/${conversationId}/?${param}`;
  },
  SPARK_POOL: (authValue, isGuest = false) => {
    const param = isGuest
      ? `ticket=${encodeURIComponent(authValue)}`
      : `token=${encodeURIComponent(authValue)}`;
    return `${WS_BASE_URL}/ws/spark_pool/?${param}`;
  },
};

export const WS_CHANNELS = {
  CHAT: "chat",
  SPARK_POOL: "spark_pool",
};

export const STORAGE_KEYS = {
  ACCESS_TOKEN: "access_token",
  AUTH_USER: "auth_user",
  SPARK_POOL: "spark_pool",
  CHARACTER_DRAFT: "character_draft",
};

export const COOKIES = {
  CSRF_TOKEN: "eaaic_csrftoken",
};
