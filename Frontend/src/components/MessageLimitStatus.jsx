import { useLanguage } from "../context/LanguageContext";
import { characterCount, USER_MESSAGE_LIMIT } from "../utils/textLimits";

export default function MessageLimitStatus({ value, id }) {
  const { t } = useLanguage();
  const count = characterCount(value);
  return (
    <>
      <p id={id} role="status" className="text-xs theme-text-muted">
        {t("chat.messageCount", { count, limit: USER_MESSAGE_LIMIT })}
      </p>
      {count > USER_MESSAGE_LIMIT && (
        <p role="alert" className="text-xs text-rose-500">
          {t("chat.messageTooLong")}
        </p>
      )}
    </>
  );
}
