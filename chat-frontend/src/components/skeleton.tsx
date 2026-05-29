"use client";

interface SkeletonProps {
  className?: string;
}

/** Bloco animado pra placeholder de carregamento. */
export function Skeleton({ className = "" }: SkeletonProps) {
  return (
    <div
      className={[
        "animate-pulse rounded-md",
        "bg-gray-200 dark:bg-zinc-800/70",
        className,
      ].join(" ")}
    />
  );
}

/** Skeleton de mensagem do assistente. */
export function MessageSkeleton() {
  return (
    <div className="w-full flex gap-3">
      <Skeleton className="w-8 h-8 rounded-full shrink-0" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-5/6" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </div>
  );
}

/** Skeleton de item de thread na sidebar. */
export function ThreadSkeleton() {
  return (
    <div className="px-3 py-2 flex items-start gap-2">
      <Skeleton className="w-3 h-3 rounded-sm mt-1" />
      <Skeleton className="h-3 flex-1" />
    </div>
  );
}
