import { useEffect, useLayoutEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";

export default function ForegroundPushNotice({ conversationId }) {
  const { user, sessionGeneration, isAuthenticated } = useAuth();
  if (!isAuthenticated || !conversationId) return null;
  return (
    <ScopedNotice
      key={`${user?.user_id}:${sessionGeneration}:${conversationId}`}
      conversationId={conversationId}
    />
  );
}

function ScopedNotice({ conversationId }) {
  const [receipt, setReceipt] = useState(null);
  const { t } = useLanguage();
  useEffect(() => {
    if (!navigator.serviceWorker) return;
    const receive = (event) => {
      if (
        event.data?.type !== "UWENZAI_PUSH_RECEIPT" ||
        document.visibilityState !== "visible"
      )
        return;
      const payload = event.data.payload;
      if (
        !payload ||
        payload.url !== `/conversations/${conversationId}` ||
        typeof payload.title !== "string" ||
        typeof payload.body !== "string" ||
        !event.ports?.[0]
      )
        return;
      setReceipt({ payload, port: event.ports[0] });
    };
    navigator.serviceWorker.addEventListener("message", receive);
    return () =>
      navigator.serviceWorker.removeEventListener("message", receive);
  }, [conversationId]);
  useLayoutEffect(() => {
    if (receipt && document.visibilityState === "visible") {
      receipt.port.postMessage({ type: "UWENZAI_PUSH_HANDLED" });
      receipt.port.close();
    }
  }, [receipt]);
  if (!receipt) return null;
  return (
    <div
      role="status"
      className="mx-4 mt-2 rounded-xl border p-2 text-sm theme-text"
    >
      <p>{receipt.payload.title}</p>
      <p>{receipt.payload.body}</p>
      <button
        type="button"
        className="underline"
        onClick={() => setReceipt(null)}
      >
        {t("chat.dismissNotification")}
      </button>
    </div>
  );
}
