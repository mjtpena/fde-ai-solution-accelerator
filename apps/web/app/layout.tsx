import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FDE AI Solution Accelerator",
  description: "Web baseline for the FDE AI Solution Accelerator.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
