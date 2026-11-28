package event_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"sync"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/event"
	"github.com/agent-fox-dev/nightshift/internal/store"
	"github.com/agent-fox-dev/nightshift/internal/store/storetest"
)

var day0 = time.Date(2026, 11, 1, 12, 0, 0, 0, time.UTC)

// clock is a time the test moves by hand.
type clock struct {
	mu  sync.Mutex
	now time.Time
}

func newClock(t time.Time) *clock { return &clock{now: t} }

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *clock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = t
}

func openLog(t *testing.T, st *store.Store, c *clock) *event.Log {
	t.Helper()
	l, err := event.Open(st, c.Now)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	return l
}

func appendEvent(t *testing.T, l *event.Log, typ string) event.Event {
	t.Helper()
	ev, err := l.Append(context.Background(), typ, map[string]string{"type": typ})
	if err != nil {
		t.Fatalf("Append(%q): %v", typ, err)
	}
	return ev
}

func seqs(events []event.Event) []int64 {
	out := make([]int64, len(events))
	for i, ev := range events {
		out[i] = ev.Seq
	}
	return out
}

func TestAppend_GivesGaplessSeqsToConcurrentCallers(t *testing.T) {
	t.Parallel()
	l := openLog(t, storetest.Open(t), newClock(day0))
	const callers, each = 8, 50

	type appended struct {
		seq  int64
		typ  string
		data string
	}
	results := make(chan appended, callers*each)
	var wg sync.WaitGroup
	for g := range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range each {
				typ := fmt.Sprintf("test.%d", g)
				data := map[string]int{"caller": g, "i": i}
				ev, err := l.Append(context.Background(), typ, data)
				if err != nil {
					t.Errorf("Append: %v", err)
					return
				}
				want, _ := json.Marshal(data)
				results <- appended{seq: ev.Seq, typ: typ, data: string(want)}
			}
		}()
	}
	wg.Wait()
	close(results)

	bySeq := map[int64]appended{}
	for r := range results {
		if _, dup := bySeq[r.seq]; dup {
			t.Errorf("seq %d given twice", r.seq)
		}
		bySeq[r.seq] = r
	}
	got := make([]int64, 0, len(bySeq))
	for seq := range bySeq {
		got = append(got, seq)
	}
	sort.Slice(got, func(i, j int) bool { return got[i] < got[j] })
	for i, seq := range got {
		if seq != int64(i+1) {
			t.Fatalf("seqs given = %v, want 1..%d", got, callers*each)
		}
	}
	if len(got) != callers*each {
		t.Fatalf("%d seqs given, want %d", len(got), callers*each)
	}

	kept, err := l.After(context.Background(), 0)
	if err != nil {
		t.Fatalf("After(0): %v", err)
	}
	if len(kept) != callers*each {
		t.Fatalf("After(0) returned %d events, want %d", len(kept), callers*each)
	}
	for i, ev := range kept {
		want := bySeq[int64(i+1)]
		if ev.Seq != int64(i+1) || ev.Type != want.typ || string(ev.Data) != want.data {
			t.Errorf("event %d = {%d %q %s}, want {%d %q %s}", i, ev.Seq, ev.Type, ev.Data, i+1, want.typ, want.data)
		}
		if !ev.Time.Equal(day0) {
			t.Errorf("event %d time = %v, want %v", ev.Seq, ev.Time, day0)
		}
	}
}

func TestPrune_RemovesEventsOlderThanSevenDays(t *testing.T) {
	t.Parallel()
	c := newClock(day0)
	l := openLog(t, storetest.Open(t), c)
	old := appendEvent(t, l, "test.day0")
	c.Set(day0.AddDate(0, 0, 3))
	young := appendEvent(t, l, "test.day3")
	c.Set(day0.AddDate(0, 0, 8))
	newest := appendEvent(t, l, "test.day8")

	c.Set(day0.AddDate(0, 0, 8).Add(time.Minute))
	if err := l.Prune(context.Background()); err != nil {
		t.Fatalf("Prune: %v", err)
	}

	_, err := l.After(context.Background(), 0)
	var expired *event.CursorExpiredError
	if !errors.As(err, &expired) || expired.OldestSeq != young.Seq {
		t.Errorf("After(0) error = %v, want cursor expired with oldest seq %d", err, young.Seq)
	}
	kept, err := l.After(context.Background(), old.Seq)
	if err != nil {
		t.Fatalf("After(%d): %v", old.Seq, err)
	}
	if got, want := fmt.Sprint(seqs(kept)), fmt.Sprint([]int64{young.Seq, newest.Seq}); got != want {
		t.Errorf("After(%d) seqs = %s, want %s", old.Seq, got, want)
	}
	if next := appendEvent(t, l, "test.next"); next.Seq != newest.Seq+1 {
		t.Errorf("the next Append got seq %d, want %d", next.Seq, newest.Seq+1)
	}
}

func TestLog_KeepsEventsAndSeqAcrossReopen(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// use appends to the log before the store is closed, moving c.
		use       func(t *testing.T, l *event.Log, c *clock)
		after     int64
		wantAfter []int64
		wantNext  int64
	}{
		{
			name: "kept events",
			use: func(t *testing.T, l *event.Log, c *clock) {
				for range 5 {
					appendEvent(t, l, "test.kept")
				}
			},
			after:     2,
			wantAfter: []int64{3, 4, 5},
			wantNext:  6,
		},
		{
			name: "every event pruned",
			use: func(t *testing.T, l *event.Log, c *clock) {
				for range 3 {
					appendEvent(t, l, "test.pruned")
				}
				c.Set(day0.AddDate(0, 0, 8))
				if err := l.Prune(context.Background()); err != nil {
					t.Fatalf("Prune: %v", err)
				}
			},
			after:     3,
			wantAfter: []int64{},
			wantNext:  4,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			path := storetest.Path(t)
			c := newClock(day0)
			st, err := store.Open(path)
			if err != nil {
				t.Fatalf("store.Open: %v", err)
			}
			tt.use(t, openLog(t, st, c), c)
			if err := st.Close(); err != nil {
				t.Fatalf("Close: %v", err)
			}

			st, err = store.Open(path)
			if err != nil {
				t.Fatalf("store.Open again: %v", err)
			}
			defer st.Close()
			l := openLog(t, st, c)
			kept, err := l.After(context.Background(), tt.after)
			if err != nil {
				t.Fatalf("After(%d): %v", tt.after, err)
			}
			if got, want := fmt.Sprint(seqs(kept)), fmt.Sprint(tt.wantAfter); got != want {
				t.Errorf("After(%d) seqs = %s, want %s", tt.after, got, want)
			}
			if next := appendEvent(t, l, "test.next"); next.Seq != tt.wantNext {
				t.Errorf("Append after the reopen got seq %d, want %d", next.Seq, tt.wantNext)
			}
		})
	}
}

func TestAppend_DropsASubscriberThatStopsReading(t *testing.T) {
	t.Parallel()
	l := openLog(t, storetest.Open(t), newClock(day0))
	reading, err := l.Subscribe(context.Background(), 0)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	defer reading.Close()
	stalled, err := l.Subscribe(context.Background(), 0)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	defer stalled.Close()
	const total = 300

	received := make(chan int, 1)
	go func() {
		n := 0
		for range reading.C {
			if n++; n == total {
				break
			}
		}
		received <- n
	}()
	for i := range total {
		start := time.Now()
		appendEvent(t, l, "test.flood")
		if took := time.Since(start); took >= 100*time.Millisecond {
			t.Errorf("Append %d took %v, want under 100ms", i+1, took)
		}
	}

	select {
	case n := <-received:
		if n != total {
			t.Errorf("the reading subscriber received %d events, want %d", n, total)
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("the reading subscriber did not receive %d events", total)
	}
	waiting := 0
	for closed := false; !closed; {
		select {
		case _, ok := <-stalled.C:
			if ok {
				waiting++
			} else {
				closed = true
			}
		case <-time.After(time.Second):
			t.Fatalf("the stalled subscriber's channel is still open after %d events", waiting)
		}
	}
	if waiting != event.Buffer {
		t.Errorf("the stalled subscriber held %d events before it was closed, want %d", waiting, event.Buffer)
	}
	if n := l.Subscribers(); n != 1 {
		t.Errorf("Subscribers() = %d, want 1", n)
	}
}
