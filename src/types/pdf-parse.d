declare module 'pdf-parse' {
  const pdfParse: (data: ArrayBuffer | Buffer) => Promise<{ text: string }>;
  export default pdfParse;
}