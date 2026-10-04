import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { GoogleSetup } from '../src/features/google/GoogleSetup';
import type { GoogleStatus } from '../src/types';

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const status = (connected: boolean): GoogleStatus => ({
  configured: true,
  connected,
  message: connected ? 'Google account connected.' : 'Complete account consent.',
  scopes: [],
});

describe('Google connection checks', () => {
  it('uses one caught status request per check and reports the returned connection state', async () => {
    let resolve!: (value: Response) => void;
    const pending = new Promise<Response>((success) => {
      resolve = success;
    });
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(json(status(false)))
      .mockReturnValueOnce(pending);
    vi.stubGlobal('fetch', fetcher);
    const connected = vi.fn();
    const user = userEvent.setup();
    render(<GoogleSetup onConnected={connected} />);
    await screen.findByText('Credentials imported');
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(screen.getByRole('button', { name: 'Check connection' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => resolve(json(status(true))));
    expect(screen.getByText('Google account connected.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Refresh status' })).toBeEnabled();
    expect(connected).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      '/api/google/status',
      '/api/google/status',
    ]);
  });

  it.each([false, true])(
    'shows a genuine refresh failure and allows retry (initial connected=%s)',
    async (initialConnected) => {
      const fetcher = vi
        .fn()
        .mockResolvedValueOnce(json(status(initialConnected)))
        .mockRejectedValueOnce(new Error('Google status service is unreachable.'))
        .mockResolvedValueOnce(json(status(true)));
      vi.stubGlobal('fetch', fetcher);
      const connected = vi.fn();
      const user = userEvent.setup();
      render(<GoogleSetup onConnected={connected} />);
      const buttonName = initialConnected ? 'Refresh status' : 'Check connection';
      await waitFor(() => expect(screen.getByRole('button', { name: buttonName })).toBeEnabled());
      await user.click(screen.getByRole('button', { name: buttonName }));
      expect(screen.getByRole('alert')).toHaveTextContent('Google status service is unreachable.');
      expect(screen.getByRole('button', { name: buttonName })).toBeEnabled();
      expect(connected).not.toHaveBeenCalled();
      expect(fetcher).toHaveBeenCalledTimes(2);
      await user.click(screen.getByRole('button', { name: buttonName }));
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.getByText('Google account connected.')).toBeVisible();
      expect(connected).toHaveBeenCalledTimes(1);
      expect(fetcher).toHaveBeenCalledTimes(3);
    },
  );
});
