import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Renders GitHub-flavoured markdown (tables, lists, code, links) safely. */
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children || ""}</ReactMarkdown>
    </div>
  );
}
