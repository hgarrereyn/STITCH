from http.server import SimpleHTTPRequestHandler, HTTPServer

class CORSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Add CORS headers
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

def main(args):
    server = HTTPServer(("0.0.0.0", args.port), CORSRequestHandler)
    print(f"Serving on port {args.port}")
    server.serve_forever()

def register(subparsers):
    parser = subparsers.add_parser('serve', help='Start a local server for serving files')
    parser.add_argument('port', type=int, help='Port to serve on')
    parser.set_defaults(func=main)

if __name__ == "__main__":
    # For standalone testing
    import argparse
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

