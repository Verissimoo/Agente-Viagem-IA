import "@/styles/globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Passagens com Desconto — Atendente",
  description: "Cotação inteligente de passagens aéreas para vendedores.",
};

// Script inline aplicado ANTES do React montar — evita flash claro.
// Default: dark se OS prefere dark; senão respeita localStorage.
const themeBootScript = `
(function() {
  try {
    var saved = localStorage.getItem('pcd-chat-theme');
    var theme = (saved === 'light' || saved === 'dark')
      ? saved
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'dark');
    if (theme === 'dark') document.documentElement.classList.add('dark');
  } catch(e) {}
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pt-BR" className={`dark ${inter.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body className="h-full font-sans antialiased">{children}</body>
    </html>
  );
}
