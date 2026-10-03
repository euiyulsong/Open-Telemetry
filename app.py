import argparse
import time

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def main(n: int):
    provider = TracerProvider()
    trace.set_tracer_provider(provider)

    exporter = OTLPSpanExporter(
        endpoint="http://localhost:4318/v1/traces",
        timeout=3,
    )

    processor = BatchSpanProcessor(
        exporter,
        max_queue_size=2048,
        max_export_batch_size=64,
        schedule_delay_millis=500,
    )

    provider.add_span_processor(processor)
    tracer = trace.get_tracer("failure-lab")

    for i in range(n):
        with tracer.start_as_current_span(f"span-{i}") as span:
            span.set_attribute("test.index", i)
            span.set_attribute("test.message", "hello")
            time.sleep(0.005)

    print(f"generated {n} spans")

    ok = provider.force_flush(timeout_millis=5000)
    print("force_flush:", ok)

    provider.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=100)
    args = parser.parse_args()
    main(args.n)
