"use client";

interface AvatarProps {
  name: string;
  size?: "sm" | "md";
}

export default function Avatar({ name, size = "md" }: AvatarProps) {
  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("") || "?";

  const dim = size === "sm" ? "w-8 h-8 text-xs" : "w-9 h-9 text-sm";

  return (
    <div
      className={[
        dim,
        "shrink-0 rounded-full bg-gradient-to-br from-brand-500 to-brand-700",
        "flex items-center justify-center text-white font-semibold",
        "ring-1 ring-black/10",
      ].join(" ")}
    >
      {initials}
    </div>
  );
}
