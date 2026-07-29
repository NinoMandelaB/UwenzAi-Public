export default function PrimaryButton({
  children,
  variant = "primary",
  fullWidth = false,
  className = "",
  ...props
}) {
  const baseStyles =
    "rounded-2xl px-4 py-2.5 text-sm font-medium transition duration-150";

  const variantStyles =
    variant === "primary"
      ? "border"
      : variant === "secondary"
        ? "border theme-card theme-text hover:opacity-90"
        : variant === "danger"
          ? "border text-[var(--color-danger)] hover:opacity-90"
          : variant === "ghost"
            ? "border hover:opacity-90"
            : "border";

  const widthStyles = fullWidth ? "w-full" : "";

  const inlineStyles =
    variant === "primary"
      ? {
          borderColor: "var(--color-accent-soft)",
          backgroundColor: "var(--color-accent-soft)",
          color: "var(--color-accent)",
        }
      : variant === "danger"
        ? {
            borderColor:
              "color-mix(in srgb, var(--color-danger) 30%, transparent)",
            backgroundColor:
              "color-mix(in srgb, var(--color-danger) 10%, transparent)",
          }
        : variant === "ghost"
          ? {
              borderColor: "var(--color-accent-soft)",
              backgroundColor: "transparent",
              color: "var(--color-accent)",
            }
          : undefined;

  return (
    <button
      className={`${baseStyles} ${variantStyles} ${widthStyles} ${className}`}
      style={inlineStyles}
      {...props}
    >
      {children}
    </button>
  );
}
