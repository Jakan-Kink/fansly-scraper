# Database Design and Testing Documentation

## Database Design Decisions

### Foreign Key Constraints

Foreign key constraints are intentionally disabled in certain operations for the following reasons:

1. **API Data Ordering**: The Fansly API may return data in an order that doesn't match our database's foreign key constraints. For example:
   - Messages might reference users that haven't been fetched yet
   - Media items might reference posts that are still being processed
   - Group messages might arrive before the group metadata

2. **Performance**: Temporarily disabling foreign key checks during bulk operations significantly improves import performance.

3. **Recovery**: In case of partial data, we want to preserve as much information as possible rather than failing entirely.

### Group.lastMessageId and Message Querying

The `Group.lastMessageId` field serves multiple purposes:

1. **Performance Optimization**: Allows quick access to the latest message without querying the entire message table
2. **Consistency Check**: Used to verify message ordering and detect missing messages
3. **Pagination**: Helps implement efficient message pagination when combined with message timestamps

## Test Infrastructure

### Database Fixtures

1. **Base Fixtures**:
   - `temp_db_path`: Creates temporary database file
   - `config`: Configures test environment
   - `database`: Manages database lifecycle
   - `session`: Provides transactional context

2. **Entity Fixtures**:
   - `test_account`: Creates test accounts with unique IDs
   - `test_media`: Creates test media items
   - `test_message`: Creates test messages
   - `test_bundle`: Creates test media bundles

### Transaction Management

1. **Isolation Levels**:
   - Tests use `SERIALIZABLE` isolation by default
   - Specific tests may override this for performance or testing purposes

2. **Cleanup Procedures**:
   - Automatic cleanup after each test
   - Handles SQLite journal files
   - Manages foreign key constraints during cleanup

### Error Handling

1. **Constraint Violations**:
   - Tests explicitly check for appropriate error types
   - Custom error messages provide context
   - Cleanup handles partially committed data

2. **Recovery Scenarios**:
   - Tests verify system can recover from partial failures
   - Verifies data consistency after recovery
   - Tests cleanup procedures

## Performance Considerations

### Index Usage

1. **Primary Indexes**:
   - All tables have appropriate primary keys
   - Composite keys used where necessary

2. **Foreign Key Indexes**:
   - Indexes on all foreign key columns
   - Additional indexes for common query patterns

3. **Performance Testing**:
   - Bulk insert performance
   - Query optimization verification
   - Index usage verification

### Migration Performance

1. **Optimization Techniques**:
   - Batched operations
   - Temporary foreign key disabling
   - Progress tracking

2. **Testing Approach**:
   - Performance regression tests
   - Large dataset migration tests
   - Rollback performance tests

## Test Organization

### Test Categories

1. **Unit Tests**:
   - Individual model behavior
   - Constraint validation
   - Error handling

2. **Integration Tests**:
   - Complex database operations
   - Multi-table transactions
   - API data integration

3. **Performance Tests**:
   - Bulk operations
   - Query optimization
   - Index usage

4. **Edge Cases**:
   - Constraint violations
   - Recovery scenarios
   - Concurrent access

### Best Practices

1. **Test Isolation**:
   - Each test uses fresh database
   - Proper cleanup between tests
   - No test interdependencies

2. **Data Generation**:
   - Realistic test data
   - Edge case coverage
   - Performance test data sets

3. **Assertions**:
   - Specific error messages
   - Complete state verification
   - Performance thresholds

## Code Quality

### SQLAlchemy Usage

1. **Modern Patterns**:
   - Use of ORM features
   - Type annotations
   - Session management

2. **Error Handling**:
   - Specific exception types
   - Proper transaction management
   - Cleanup procedures

3. **Connection Management**:
   - Connection pooling
   - Resource cleanup
   - Error recovery

### Type Safety

1. **Type Hints**:
   - Complete model typing
   - Session type safety
   - Query result typing

2. **Validation**:
   - Input validation
   - Output validation
   - State validation

## Future Improvements

1. **Test Coverage**:
   - Add more edge case tests
   - Expand performance testing
   - Add stress testing

2. **Documentation**:
   - Expand design documentation
   - Add more code examples
   - Document common patterns

3. **Infrastructure**:
   - Improve test organization
   - Add more test utilities
   - Enhance cleanup procedures
