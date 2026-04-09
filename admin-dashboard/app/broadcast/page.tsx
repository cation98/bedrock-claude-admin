"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { sendBroadcast, BroadcastResponse } from "@/lib/api";
import { isAuthenticated } from "@/lib/auth";

type TargetMode = "all" | "specific";

export default function BroadcastPage() {
  const router = useRouter();

  const [subject, setSubject] = useState("[Otto AI] 공지");
  const [message, setMessage] = useState("");
  const [targetMode, setTargetMode] = useState<TargetMode>("all");
  const [targetInput, setTargetInput] = useState("");
  const [channelMms, setChannelMms] = useState(true);
  const [channelWs, setChannelWs] = useState(true);

  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<BroadcastResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/");
    }
  }, [router]);

  const handleSend = async () => {
    if (!message.trim()) return;

    const channels: string[] = [];
    if (channelMms) channels.push("mms");
    if (channelWs) channels.push("websocket");
    if (channels.length === 0) {
      setError("발송 채널을 하나 이상 선택해주세요.");
      return;
    }

    const targets =
      targetMode === "specific"
        ? targetInput
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [];

    setSending(true);
    setError("");
    setResult(null);

    try {
      const res = await sendBroadcast({
        message,
        subject,
        targets,
        channels,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "발송 실패");
    } finally {
      setSending(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <h1 className="mb-6 text-xl font-bold text-gray-900">공지 발송</h1>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3">
          <h2 className="text-base font-semibold text-gray-900">
            메시지 작성
          </h2>
        </div>

        <div className="space-y-5 p-4">
          {/* Subject */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              제목
            </label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
            />
          </div>

          {/* Message */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              메시지
            </label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={6}
              placeholder="공지 내용을 입력하세요..."
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none resize-y"
            />
          </div>

          {/* Target */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              발송 대상
            </label>
            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input
                  type="radio"
                  name="targetMode"
                  checked={targetMode === "all"}
                  onChange={() => setTargetMode("all")}
                  className="text-blue-600"
                />
                전체 활성 사용자
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input
                  type="radio"
                  name="targetMode"
                  checked={targetMode === "specific"}
                  onChange={() => setTargetMode("specific")}
                  className="text-blue-600"
                />
                특정 사용자
              </label>
            </div>
            {targetMode === "specific" && (
              <input
                type="text"
                value={targetInput}
                onChange={(e) => setTargetInput(e.target.value)}
                placeholder="사번을 쉼표로 구분 (예: N1234567, N7654321)"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
              />
            )}
          </div>

          {/* Channels */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              발송 채널
            </label>
            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={channelMms}
                  onChange={(e) => setChannelMms(e.target.checked)}
                  className="rounded border-gray-300 text-blue-600"
                />
                MMS 문자
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={channelWs}
                  onChange={(e) => setChannelWs(e.target.checked)}
                  className="rounded border-gray-300 text-blue-600"
                />
                WebSocket 실시간 (터미널)
              </label>
            </div>
          </div>

          {/* Send button */}
          <div className="flex justify-end pt-2">
            <button
              onClick={handleSend}
              disabled={sending || !message.trim()}
              className="rounded-md bg-blue-600 px-6 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {sending ? "발송 중..." : "발송"}
            </button>
          </div>
        </div>
      </div>

      {/* Result */}
      {result && (
        <div className="mt-6 rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-3">
            <h2 className="text-base font-semibold text-gray-900">
              발송 결과
            </h2>
          </div>
          <div className="p-4">
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div className="rounded-md bg-green-50 p-3 text-center">
                <div className="text-xs text-gray-500">MMS 성공</div>
                <div className="mt-1 text-xl font-bold text-green-600">
                  {result.mms_sent}
                </div>
              </div>
              <div className="rounded-md bg-red-50 p-3 text-center">
                <div className="text-xs text-gray-500">MMS 실패</div>
                <div className="mt-1 text-xl font-bold text-red-600">
                  {result.mms_failed}
                </div>
              </div>
              <div className="rounded-md bg-blue-50 p-3 text-center">
                <div className="text-xs text-gray-500">WebSocket 전송</div>
                <div className="mt-1 text-xl font-bold text-blue-600">
                  {result.ws_sent}
                </div>
              </div>
            </div>
            {result.targets.length > 0 && (
              <div>
                <div className="text-xs font-medium text-gray-500 mb-1">
                  대상 사용자 ({result.targets.length}명)
                </div>
                <div className="flex flex-wrap gap-1">
                  {result.targets.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
