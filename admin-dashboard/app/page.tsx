"use client";

import { FormEvent, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { login, verify2fa, LoginStep1Response } from "@/lib/api";
import { setToken, setUser, isAuthenticated } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 2FA state
  const [step, setStep] = useState<"login" | "2fa">("login");
  const [codeId, setCodeId] = useState("");
  const [phoneMasked, setPhoneMasked] = useState("");
  const [tfaCode, setTfaCode] = useState("");
  const [countdown, setCountdown] = useState(300);

  useEffect(() => {
    if (isAuthenticated()) {
      router.replace("/dashboard");
    }
  }, [router]);

  // 2FA countdown timer
  useEffect(() => {
    if (step !== "2fa") return;
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [step, countdown]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await login({ username, password });

      // Check if 2FA is needed (login endpoint may return LoginStep1Response instead)
      const maybeStep1 = res as unknown as LoginStep1Response;
      if (maybeStep1.requires_2fa) {
        setCodeId(maybeStep1.code_id);
        setPhoneMasked(maybeStep1.phone_masked);
        setStep("2fa");
        setCountdown(300);
        return;
      }

      // No 2FA - proceed directly
      setToken(res.access_token);
      setUser({ username: res.username, name: res.name, role: res.role });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify() {
    if (tfaCode.length !== 6) {
      setError("6자리 인증코드를 입력해주세요.");
      return;
    }
    setLoading(true);
    setError("");

    try {
      const res = await verify2fa({ code_id: codeId, code: tfaCode });
      setToken(res.access_token);
      setUser({ username: res.username, name: res.name, role: res.role });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "인증 실패");
      setTfaCode("");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="rounded-lg border border-gray-200 bg-white p-8 shadow-sm">
          <h1 className="mb-1 text-center text-xl font-bold text-gray-900">
            Claude Code Platform
          </h1>
          <p className="mb-6 text-center text-sm text-gray-500">
            사내 SSO 계정으로 로그인
          </p>

          {error && (
            <p className="mb-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
              {error}
            </p>
          )}

          {step === "2fa" ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                {phoneMasked} 로 발송된 인증코드를 입력하세요
              </p>
              <div>
                <label
                  htmlFor="tfa-code"
                  className="mb-1 block text-sm font-medium text-gray-700"
                >
                  인증코드
                </label>
                <input
                  id="tfa-code"
                  type="text"
                  maxLength={6}
                  inputMode="numeric"
                  pattern="[0-9]*"
                  autoComplete="one-time-code"
                  value={tfaCode}
                  onChange={(e) =>
                    setTfaCode(e.target.value.replace(/\D/g, ""))
                  }
                  onKeyDown={(e) => e.key === "Enter" && handleVerify()}
                  placeholder="000000"
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 text-center text-2xl tracking-[0.5em] font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  autoFocus
                />
              </div>
              <p className="text-sm text-gray-500">
                남은 시간: {Math.floor(countdown / 60)}:
                {String(countdown % 60).padStart(2, "0")}
                {countdown <= 0 && (
                  <span className="ml-2 text-red-500">만료됨</span>
                )}
              </p>
              <button
                type="button"
                onClick={handleVerify}
                disabled={loading || tfaCode.length !== 6 || countdown <= 0}
                className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
              >
                {loading ? "확인 중..." : "인증 확인"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setStep("login");
                  setTfaCode("");
                  setCodeId("");
                  setError("");
                }}
                className="w-full rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition-colors"
              >
                로그인으로 돌아가기
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label
                  htmlFor="username"
                  className="mb-1 block text-sm font-medium text-gray-700"
                >
                  사용자명
                </label>
                <input
                  id="username"
                  type="text"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="admin"
                  autoComplete="username"
                />
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="mb-1 block text-sm font-medium text-gray-700"
                >
                  비밀번호
                </label>
                <input
                  id="password"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="********"
                  autoComplete="current-password"
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? "로그인 중..." : "로그인"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
