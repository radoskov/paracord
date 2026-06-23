<script lang="ts">
  const columns = ['Title', 'Authors', 'Year', 'Shelves', 'Tags', 'Status'];
</script>

<table>
  <thead>
    <tr>{#each columns as column}<th>{column}</th>{/each}</tr>
  </thead>
  <tbody>
    <tr><td colspan="6">Paper table placeholder</td></tr>
  </tbody>
</table>
