import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

// Monoespaciada para identificadores, versiones de prompt, códigos de política
// y trazas: datos que se copian y se comparan carácter a carácter.
const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pharma Commercial AI Sandbox",
  description:
    "Entorno de demostración con datos exclusivamente sintéticos. " +
    "Sistema de IA comercial farmacéutica con trazabilidad y supervisión humana.",
  // No se indexa. Es una demostración con datos sintéticos, y aparecer en
  // buscadores junto a nombres de producto inventados invita justo a la
  // confusión que el aviso de entorno intenta evitar.
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${inter.variable} ${plexMono.variable} h-full`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
