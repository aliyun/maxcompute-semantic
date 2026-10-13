// Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
// SPDX-License-Identifier: Apache-2.0

// Generate the synthetic native JSON golden from the pinned Catalog SDK in
// mcp_gateway/third_party/aliyun-odps-openapi-sdk/catalog-api/go/client.
// client.go source Git blob: 69e858de6f49ed83f2fdb10abad962e39c618273.
// Run from that Gateway checkout with its Go workspace:
// GOFLAGS=-mod=readonly go run <this-file> > catalog-sdk-native-json.golden.json
// No HTTP, MaxCompute, model, or credential calls are made. The futureModel
// fixture alone adds a synthetic typed map extension to verify preservation.

package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"

	catalog "github.com/aliyun/aliyun-odps-openapi-sdk/catalog-api/go/client"
)

func main() {
	sections := map[string]any{
		"dataReferences": []*catalog.DataReference{{Project: new("warehouse"), Schema: new("default"), Table: new("orders"), TableId: new("table-1")}},
		"semanticModel": &catalog.SemanticModel{
			LogicalTables:         []*catalog.LogicalTable{{Name: new("orders"), BaseTable: new("warehouse.default.orders"), PrimaryKey: &catalog.LogicalTablePrimaryKey{Columns: []*string{new("id")}}, Dimensions: []*catalog.Dimension{{Name: new("country"), Expr: new("country"), DataType: new("STRING"), Description: new("synthetic <&>\u2028"), EnumValues: []*catalog.EnumValue{{Value: new("A"), LabelCn: new("甲")}}}}, TimeDimensions: []*catalog.TimeDimension{{Name: new("date"), Expr: new("date"), DataType: new("DATE")}}}},
			NamedFilters:          []*catalog.NamedFilter{{Name: new("paid"), SqlFragment: new("paid = true"), ApplicableTables: []*string{new("orders")}}},
			ApprovedRelationships: []*catalog.ApprovedRelationship{{Name: new("owner"), LeftTable: new("orders"), RightTable: new("customers"), RelationshipColumns: []*catalog.RelationshipColumn{{LeftColumn: new("owner_id"), RightColumn: new("id")}}, SourceDetail: &catalog.RelationshipSourceDetail{Type: new("MANUAL"), Confidence: new(0.9)}}},
		},
		"instructions":      &catalog.Instructions{GeneralGuidance: new("synthetic <&>"), DefaultFilters: []*catalog.DefaultFilter{{Scope: new("TABLE"), Table: new("orders"), SqlFragment: new("paid = true"), Description: new("synthetic")}}, BusinessRules: []*catalog.BusinessRule{{Id: new("r1"), Description: new("synthetic"), AffectedMetrics: []*string{new("gmv")}}}},
		"metricDefinitions": []*catalog.MetricDefinition{{Name: new("gmv"), Description: new("synthetic"), SemanticType: new("MEASURE"), BaseLogicalTable: new("orders"), Expr: new("amount"), DataType: new("DOUBLE"), DefaultAggregation: new("SUM"), Format: &catalog.MetricFormat{Type: new("NUMBER"), DecimalPlaces: new(int32(2)), ThousandsSeparator: new(true)}}, {Name: new("ratio"), SemanticType: new("CALCULATED_METRIC"), DerivedMetric: &catalog.DerivedMetric{Formula: new("gmv / count"), RefMetrics: []*string{new("gmv"), new("count")}, ResultDataType: new("DOUBLE")}}},
		"verifiedQueries":   []*catalog.VerifiedQuery{{Name: new("sample"), Question: new("synthetic fixture"), MatchPolicy: &catalog.MatchPolicy{RequireExactQuestion: new(true), SimilarityThreshold: new(0.75), FallbackOnMiss: new("NONE")}, Sql: new("select 1"), Parameters: []*catalog.VqParameter{{Name: new("value"), Type: new("STRING"), Required: new(false), Constraints: &catalog.VqParameterConstraints{MinValue: new(1e-6), MaxValue: new(10.0), Pattern: new("synthetic"), Nullable: new(true)}}}, Trusted: new(true), VerifiedAt: new(int64(1234)), Provenance: new("SYNTHETIC_FIXTURE"), CostEstimate: &catalog.CostEstimate{Method: new("STATIC"), Complexity: new(1e-7), Udf: new(int32(0)), InputBytes: new(int64(12)), Reference: new(false)}}},
		"glossary":          []*catalog.GlossaryTerm{{Term: new("业务"), Definition: new("synthetic"), AlsoKnownAs: []*string{new("term")}, ApplicableTables: []*string{new("orders")}, LinkedMetric: new("gmv"), Usage: new("synthetic")}},
		"benchmarks":        &catalog.Benchmarks{Questions: []*catalog.BenchmarkQuestion{{Id: new("q1"), Status: new("ACTIVE"), Question: new("synthetic"), ExpectedSemantics: &catalog.ExpectedSemantics{MetricName: new("gmv"), DimensionNames: []*string{new("country")}, TimeWindow: &catalog.TimeWindow{Type: new("ROLLING_DAYS"), Value: new(int32(1))}, DimensionFilters: []*catalog.DimensionFilter{{DimensionName: new("country"), Op: new("EQ"), Value: new("A")}}}, ExpectedSql: new("select 1"), ExpectedResultMatchMode: new("NONE"), Tags: []*string{new("synthetic")}}}},
	}
	sections["futureModel"] = struct {
		LogicalTables   []*catalog.LogicalTable `json:"logicalTables,omitempty"`
		ServerExtension map[string]any          `json:"serverExtension,omitempty"`
	}{
		LogicalTables:   []*catalog.LogicalTable{{Name: new("orders"), BaseTable: new("warehouse.default.orders")}},
		ServerExtension: map[string]any{"z": "last", "a": map[string]any{"z": "last", "a": "first"}},
	}
	golden := make(map[string]any)
	for section, typed := range sections {
		native, err := json.Marshal(typed)
		if err != nil {
			panic(err)
		}
		var projected any
		if err = json.Unmarshal(native, &projected); err != nil {
			panic(err)
		}
		golden[section] = map[string]any{"native_json": string(native), "snapshot": map[string]any{"section": section, "present": true, "size_bytes": len(native), "content_hash": fmt.Sprintf("sha256:%x", sha256.Sum256(native)), "content": projected}}
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(golden); err != nil {
		panic(err)
	}
}
