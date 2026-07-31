// src/utils/messageLinks.jsx

/**
 * Renders markdown links and bare findahelpline.com URLs inside chat message
 * text. Shared by the modal chat and the full-page chat so a system message
 * (e.g. the guest out-of-sparks notice) renders the same clickable link in both.
 *
 * AI-authored messages only get links for findahelpline.com, so a character
 * cannot talk a user into following an arbitrary URL.
 */
function isFindAHelplineUrl(href) {
  try {
    const url = new URL(href);
    return (
      url.hostname === "findahelpline.com" ||
      url.hostname === "www.findahelpline.com"
    );
  } catch {
    return false;
  }
}

export function parseLinks(text, navigate, senderType) {
  const parts = [];
  const mdRegex = /\[([^\]]+)\]\(([^)]+)\)/g;
  let lastIndex = 0;
  let match;

  while ((match = mdRegex.exec(text)) !== null) {
    const [, label, href] = match;
    if (senderType === "ai" && !isFindAHelplineUrl(href)) {
      parts.push(text.slice(lastIndex, mdRegex.lastIndex));
      lastIndex = mdRegex.lastIndex;
      continue;
    }
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const isInternal = href.startsWith("/");
    parts.push(
      <a
        key={`md-${match.index}`}
        href={isInternal ? undefined : href}
        onClick={
          isInternal && navigate
            ? (e) => {
                e.preventDefault();
                navigate(href);
              }
            : undefined
        }
        style={{
          color: "var(--color-accent)",
          textDecoration: "underline",
          cursor: "pointer",
          fontWeight: 600,
        }}
        rel={isInternal ? undefined : "noopener noreferrer"}
        target={isInternal ? undefined : "_blank"}
      >
        {label}
      </a>,
    );
    lastIndex = mdRegex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  const result = parts.length > 0 ? parts : [text];

  if (senderType === "ai" || senderType === "system") {
    const bareUrlPattern =
      /<?(https?:\/\/(?:www\.)?findahelpline\.com[^\s>)\]]*?)>?/g;

    return result.flatMap((part, partIndex) => {
      if (typeof part !== "string") return [part];

      const segments = [];
      let lastIdx = 0;
      let m;
      bareUrlPattern.lastIndex = 0;

      while ((m = bareUrlPattern.exec(part)) !== null) {
        if (m.index > lastIdx) {
          segments.push(part.slice(lastIdx, m.index));
        }
        const href = m[1];
        segments.push(
          <a
            key={`bare-${partIndex}-${m.index}`}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: "var(--color-accent)",
              textDecoration: "underline",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            {href}
          </a>,
        );
        lastIdx = bareUrlPattern.lastIndex;
      }

      if (lastIdx < part.length) {
        segments.push(part.slice(lastIdx));
      }

      return segments.length > 0 ? segments : [part];
    });
  }

  return result;
}
