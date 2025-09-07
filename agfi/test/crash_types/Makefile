CC = clang
CFLAGS = -g -fsanitize=address
LDFLAGS = -fsanitize=address

SRCS := $(wildcard *.c)
BINS := $(SRCS:.c=)

all: $(BINS)

%: %.c
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

clean:
	rm -f $(BINS)

# Run crash summary on each compiled binary
test: CFLAGS += -w
test: all
	@for bin in $(BINS); do \
		printf "\n=== Running crash-summary on %s ===\n" "$$bin"; \
		agfi crash-summary $(CURDIR)/$$bin; \
	done

.PHONY: all clean test
