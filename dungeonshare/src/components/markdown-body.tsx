import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownBody({ children }: { children: string }) {
  return (
    <ReactMarkdown
      components={{
        h1: ({ children: heading }) => <h3>{heading}</h3>,
        h2: ({ children: heading }) => <h4>{heading}</h4>,
      }}
      remarkPlugins={[remarkGfm]}
      skipHtml
    >
      {children}
    </ReactMarkdown>
  );
}
