import numpy as np
# import TrkFile

def compute_matching_uncertainty(trk):
  """
  For each consecutive frame pair (t, t+1), compute the matching uncertainty
  of every detection using the same L1 cost matrix as match_frame in
  link_trajectories.py.

  Matching uncertainty for detection j in the current frame is the ratio:
      best_match_cost / second_best_match_cost

  It is computed from two perspectives and the minimum (most confident) is kept:

    Forward  (j -> next frame): ratio of j's 1st vs 2nd closest in t+1.
    Backward (best_next -> curr frame): ratio of best_next's 1st vs 2nd
             closest in t, where best_next is j's closest detection in t+1.

  The `second_ids` array records what competed with the winning match:
    - Forward win : second_ids[i,j] is the 2nd closest detection in t+1
                    (global target index, in the next frame).
    - Backward win: second_ids[i,j] is the 2nd closest detection in t
                    (global target index, in the current frame).

  Parameters
  ----------
  trk : TrkFile.Trk

  Returns
  -------
  uncertainty : np.ndarray float, shape (T-1, max_detections)
      Per-detection matching uncertainty (best/second ratio). NaN when the
      ratio cannot be formed or the slot is unused.
  is_forward : np.ndarray bool, shape (T-1, max_detections)
      True when the forward ratio was <= the backward ratio (or only the
      forward ratio was available). False when the backward ratio won.
  curr_ids : np.ndarray int, shape (T-1, max_detections)
      Global target index of local detection j in frame t. -1 unused.
  best_next_ids : np.ndarray int, shape (T-1, max_detections)
      Global target index of j's closest detection in frame t+1. -1 unavailable.
  second_ids : np.ndarray int, shape (T-1, max_detections)
      Global target index of the detection that competed with the winning match.
      From next frame if is_forward, from current frame if not. -1 unavailable.
  frame_indices : np.ndarray int, shape (T-1,)
      Absolute frame number of the current frame for each row.
  """
  T0 = trk.T0
  T1 = trk.T1
  n_pairs = T1 - T0
  frame_indices = np.arange(T0, T1, dtype=int)

  # First pass: find the maximum number of real detections in any frame
  max_detections = 0
  for t in range(T0, T1 + 1):
    p = trk.getframe(t)
    max_detections = max(max_detections, int(np.count_nonzero(trk.real_idx(p))))

  if max_detections == 0:
    empty_f = np.full((n_pairs, 0), np.nan)
    empty_b = np.full((n_pairs, 0), False)
    empty_i = np.full((n_pairs, 0), -1, dtype=int)
    return empty_f, empty_b, empty_i, empty_i, empty_i, frame_indices

  uncertainty   = np.full((n_pairs, max_detections), np.nan)
  is_forward    = np.full((n_pairs, max_detections), False, dtype=bool)
  curr_ids      = np.full((n_pairs, max_detections), -1, dtype=int)
  best_next_ids = np.full((n_pairs, max_detections), -1, dtype=int)
  second_ids    = np.full((n_pairs, max_detections), -1, dtype=int)

  for i, t in enumerate(range(T0, T1)):
    pcurr = trk.getframe(t)
    pnext = trk.getframe(t + 1)

    idxcurr = trk.real_idx(pcurr)
    idxnext = trk.real_idx(pnext)

    curr_det_indices = np.where(idxcurr)[0]   # global tgt ids, current frame
    next_det_indices = np.where(idxnext)[0]   # global tgt ids, next frame

    pcurr_real = pcurr[:, :, idxcurr]   # nlandmarks x d x ncurr
    pnext_real = pnext[:, :, idxnext]   # nlandmarks x d x nnext

    ncurr = pcurr_real.shape[2]
    nnext = pnext_real.shape[2]

    curr_ids[i, :ncurr] = curr_det_indices

    if ncurr == 0 or nnext == 0:
      continue

    nlandmarks = pcurr_real.shape[0]
    d = pcurr_real.shape[1]

    # Pairwise L1 cost matrix: ncurr x nnext (matches C1 in match_frame)
    p_c = np.reshape(pcurr_real, (d * nlandmarks, ncurr, 1))
    p_n = np.reshape(pnext_real, (d * nlandmarks, 1, nnext))
    C1 = np.nanmean(np.abs(p_c - p_n), axis=0) * 2   # ncurr x nnext

    for j in range(ncurr):
      row = C1[j, :]
      valid_next = np.where(~np.isnan(row))[0]
      if valid_next.size == 0:
        continue

      order_next = valid_next[np.argsort(row[valid_next])]
      local_best_next = order_next[0]
      best_next_ids[i, j] = next_det_indices[local_best_next]

      # Forward ratio: j vs next frame
      fwd_ratio = np.nan
      fwd_second_id = -1
      if order_next.size >= 2 and row[order_next[1]] > 0:
        fwd_ratio = row[order_next[0]] / row[order_next[1]]
        fwd_second_id = next_det_indices[order_next[1]]

      # Backward ratio: best_next vs current frame
      bwd_ratio = np.nan
      bwd_second_id = -1
      col = C1[:, local_best_next]
      valid_curr = np.where(~np.isnan(col))[0]
      if valid_curr.size >= 2:
        order_curr = valid_curr[np.argsort(col[valid_curr])]
        if col[order_curr[1]] > 0:
          bwd_ratio = col[order_curr[0]] / col[order_curr[1]]
          bwd_second_id = curr_det_indices[order_curr[1]]

      # Take the minimum (most confident) ratio; prefer forward on tie
      fwd_valid = not np.isnan(fwd_ratio)
      bwd_valid = not np.isnan(bwd_ratio)
      if fwd_valid and (not bwd_valid or fwd_ratio <= bwd_ratio):
        uncertainty[i, j] = fwd_ratio
        is_forward[i, j] = True
        second_ids[i, j] = fwd_second_id
      elif bwd_valid:
        uncertainty[i, j] = bwd_ratio
        is_forward[i, j] = False
        second_ids[i, j] = bwd_second_id

  return uncertainty, is_forward, curr_ids, best_next_ids, second_ids, frame_indices
