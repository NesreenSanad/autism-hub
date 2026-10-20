export default function Home() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-16 text-center">
      <h1 className="text-3xl font-bold">تواصل</h1>
      <p className="mt-3 text-lg">دليل الأطباء والمراكز المتخصصة في التوحد في مصر. قريبًا.</p>
      <p className="mt-1 text-sm opacity-70" dir="ltr">A directory of autism doctors and centres in Egypt. Coming soon.</p>
      <footer className="mt-16 text-xs opacity-60" dir="ltr">
        Map data ©{" "}
        <a className="underline" href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>
      </footer>
    </main>
  );
}
