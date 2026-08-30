function renderBoldSegments(text) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((segment, index) => {
    if (segment.startsWith("**") && segment.endsWith("**")) {
      return <strong key={index}>{segment.slice(2, -2)}</strong>;
    }
    return segment;
  });
}

export default function GuestModeUpsellText({ text, className = "" }) {
  const lines = String(text)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const introLines = lines.filter((line) => !line.startsWith("- "));
  const featureLines = lines
    .filter((line) => line.startsWith("- "))
    .map((line) => line.slice(2));

  return (
    <div className={className}>
      {introLines.map((line, index) => (
        <p key={index}>{renderBoldSegments(line)}</p>
      ))}
      {featureLines.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {featureLines.map((line, index) => (
            <li key={index}>{renderBoldSegments(line)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
