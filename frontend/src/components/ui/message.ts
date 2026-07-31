import { toast, type ToastContent, type Id } from 'react-toastify';

type Content = ToastContent;

/**
 * Ant Design `message` API surface backed by react-toastify. `loading` returns a
 * dismiss function so callers can hide the pending toast once work completes.
 */
const message = {
  success(content: Content, duration?: number) {
    toast.success(content, duration != null ? { autoClose: duration * 1000 } : undefined);
  },
  error(content: Content, duration?: number) {
    toast.error(content, duration != null ? { autoClose: duration * 1000 } : undefined);
  },
  info(content: Content, duration?: number) {
    toast.info(content, duration != null ? { autoClose: duration * 1000 } : undefined);
  },
  warning(content: Content, duration?: number) {
    toast.warning(content, duration != null ? { autoClose: duration * 1000 } : undefined);
  },
  loading(content: Content, _duration?: number): () => void {
    const id: Id = toast.loading(content);
    return () => toast.dismiss(id);
  },
};

export default message;
export { message };
